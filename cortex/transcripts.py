"""Correção determinística de nomes, sem alterar a transcrição de origem."""
import hashlib
import re
import unicodedata
from collections import Counter, defaultdict


def _fold(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.casefold())
                   if not unicodedata.combining(c))


def validate_transcript_aliases(value):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('Informe até 20 variações do nome em uma lista.')
    result, seen = [], set()
    for alias in value:
        if not isinstance(alias, str):
            raise ValueError('Cada variação do nome deve ser um texto.')
        alias = ' '.join(alias.split())
        if not alias or len(alias) > 100 or not any(c.isalpha() for c in alias):
            raise ValueError('Cada variação deve ter letras e entre 1 e 100 caracteres.')
        key = _fold(alias)
        if key not in seen:
            seen.add(key)
            result.append(alias)
    return result


def load_transcript_people(db):
    """Inclui inativos: também podem ser mencionados em reuniões e históricos."""
    from sqlalchemy import text
    rows = db.execute(text('''
        SELECT id, full_name, preferred_name, transcript_aliases
        FROM people ORDER BY id
    ''')).fetchall()
    return [{'id': r[0], 'full_name': r[1], 'preferred_name': r[2],
             'transcript_aliases': r[3]} for r in rows]


def _fold_with_offsets(value):
    """Mantém posições do original, inclusive acentos decompostos e casefold."""
    folded, starts, ends = [], [], []
    for i, char in enumerate(value):
        part = _fold(char)
        if not part:
            if ends:
                ends[-1] = i + 1
            continue
        folded.append(part)
        starts.extend([i] * len(part))
        ends.extend([i + 1] * len(part))
    return ''.join(folded), starts, ends


def _name_pattern(keys):
    alternatives = [r'[^\S\r\n]+'.join(re.escape(part) for part in key.split(' '))
                    for key in sorted(keys, key=lambda key: (-len(key), key))]
    return re.compile(r'(?<!\w)(?:' + '|'.join(alternatives) + r')(?!\w)')


def normalize_transcript(transcript, people):
    """Aplica apenas variantes cadastradas, em uma passagem e por palavras inteiras.

    Nomes oficiais têm precedência. Uma variante compartilhada por pessoas
    diferentes permanece intacta; um nome composto pode resolver a ambiguidade.
    """
    transcript = transcript or ''
    claims, replacements, official_names = defaultdict(set), {}, set()
    names = {}
    people = list(people)
    for person in people:
        pid, name = person['id'], person['full_name']
        names[pid] = name
        official_names.add(_fold(' '.join(name.split())))
        for spelling in [name, person.get('preferred_name') or '', *name.split()]:
            if spelling.strip():
                claims[_fold(' '.join(spelling.split()))].add(pid)

    for person in people:
        pid, name = person['id'], person['full_name']
        words = name.split()
        for alias in person.get('transcript_aliases') or []:
            # A validação ocorre no cadastro. Dados antigos inválidos não entram.
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias = ' '.join(alias.split())
            variants = [alias]
            # "Adriano FORN" deve virar "Adriano Forni", sem duplicar Adriano.
            if words and _fold(alias.split()[0]) != _fold(words[0]):
                variants += [' '.join(words[:i] + [alias])
                             for i in range(1, len(words))]
            for variant in variants:
                key = _fold(variant)
                claims[key].add(pid)
                replacements[key] = name

    # Nomes completos protegidos evitam corrigir parte de um nome já correto.
    keys = official_names | replacements.keys()
    corrections, ambiguities = Counter(), Counter()
    normalized = transcript
    if keys and transcript:
        folded, starts, ends = _fold_with_offsets(transcript)
        pattern = _name_pattern(keys)
        # Endereços e links não são nomes a corrigir.
        protected_matches = list(re.finditer(
            r'(?:[a-z][a-z0-9+.-]*://|www\.)\S+|[\w.+-]+@[\w.-]+'
            r'|(?:[\w-]+\.)+[\w-]{2,}(?:[/:?#]\S*)?', folded))
        # Protege também quando uma variante começa antes do nome oficial.
        protected_matches += list(_name_pattern(official_names).finditer(folded))
        protected = sorted((match.start(), match.end()) for match in protected_matches)
        pieces, cursor, protected_index = [], 0, 0
        for match in pattern.finditer(folded):
            start, end = starts[match.start()], ends[match.end() - 1]
            while protected_index < len(protected) and protected[protected_index][1] <= match.start():
                protected_index += 1
            if protected_index < len(protected) and protected[protected_index][0] < match.end():
                continue
            key = ' '.join(match.group().split())
            if key in official_names or key not in replacements:
                continue
            original = transcript[start:end]
            if len(claims[key]) != 1:
                candidates = tuple(sorted(names[pid] for pid in claims[key]))
                ambiguities[(original, candidates)] += 1
                continue
            replacement = replacements[key]
            pieces.extend([transcript[cursor:start], replacement])
            cursor = end
            corrections[(original, replacement)] += 1
        pieces.append(transcript[cursor:])
        normalized = ''.join(pieces)

    return {
        'text': normalized,
        'fingerprint': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
        'corrections': [{'original': old, 'replacement': new, 'count': count}
                        for (old, new), count in corrections.items()],
        'ambiguities': [{'original': old, 'candidates': list(candidates), 'count': count}
                        for (old, candidates), count in ambiguities.items()],
    }


def normalization_metadata(result):
    return {'version': 1, **{key: result[key] for key in
                            ('fingerprint', 'corrections', 'ambiguities')}}


def transcript_preview(transcript, people, extraction):
    result = normalize_transcript(transcript, people)
    previous = (extraction or {}).get('transcript_normalization') or {}
    fingerprint = previous.get('fingerprint')
    result['needs_reextraction'] = bool(extraction) and (
        fingerprint != result['fingerprint'] if fingerprint else
        not transcript or bool(result['corrections']))
    return result
