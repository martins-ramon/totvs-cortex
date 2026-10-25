"""Reconhece participantes cadastrados por sinais explícitos de presença/fala.

O texto original nunca é modificado. Uma simples menção a alguém não comprova
participação, e nomes compartilhados permanecem sem associação automática.
"""
import json
import re
from collections import defaultdict

from .transcripts import _fold, _fold_with_offsets, _name_pattern


_TIME = r'(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?'
_SPEAKER_PREFIX = re.compile(
    r'^[ \t]*(?:[-*>•][ \t]*)?(?:\[?' + _TIME + r'\]?[ \t]+)?$')
_SPEAKER_SUFFIX = re.compile(
    r'^[ \t]*(?:(?:\(' + _TIME + r'\)|\[' + _TIME + r'\])[ \t]*)?:')
_STANDALONE_SUFFIX = re.compile(r'^[ \t]*(?:\(' + _TIME + r'\)|\[' + _TIME + r'\])?[ \t]*$')
_TIME_LINE = re.compile(r'^[ \t]*\[?' + _TIME + r'\]?[ \t]*$')
_SPEECH = re.compile(
    r'^[ \t]+(?:disse|diz|comentou|comenta|falou|fala|apresentou|apresenta|'
    r'destacou|destaca|explicou|explica|relatou|relata|perguntou|pergunta|'
    r'respondeu|responde|pontuou|pontua|observou|observa|sugeriu|sugere|'
    r'compartilhou|compartilha|participou|participa|participaram|participam|'
    r'esteve[ \t]+presente|estava[ \t]+presente|estiveram[ \t]+presentes)(?!\w)')
_ATTENDANCE = re.compile(
    r'(?:^|[.!?;][ \t]+)[ \t]*(?:[-*>•][ \t]*)?'
    r'(?:participantes(?:[ \t]+(?:da|desta)[ \t]+reuniao)?|presentes|presencas|'
    r'participaram(?:[ \t]+(?:da|desta)[ \t]+reuniao)?|'
    r'(?:reuniao|conversa|encontro)[ \t]+com)(?!\w)[ \t]*[:–—-]?[ \t]*')
_ROSTER_GAP = re.compile(r'^[ \t,;/|&+\[\]\-*•]*(?:(?:e|com|a|o|as|os)[ \t,;/|&+]*)*$')
_ABSENT = re.compile(r'^[ \t]*(?:\([ \t]*|[-–—][ \t]*)?'
                     r'(?:ausente|ausentes|nao[ \t]+participou|nao[ \t]+compareceu)(?!\w)')
_REPORTED_MENTION = re.compile(r'(?:\bsobre|\bsegundo|\bque|\bse|\bcaso)[ \t]+$')
_PROSE_WORDS = {'sobre', 'pauta', 'plano', 'projeto', 'tema', 'citou', 'comentou',
                'disse', 'falou', 'falar', 'precisamos', 'revisar', 'ausente',
                'ausentes', 'nao', 'depois', 'amanha', 'assunto', 'objetivo'}
_NAME_PARTICLES = {'de', 'da', 'do', 'das', 'dos', 'e', 'd'}


def _roster_gap(original, *, tail=False):
    """Tolera convidados não cadastrados em entradas de lista bem delimitadas."""
    value = re.sub(r'\([^()\r\n]{0,120}\)', '', original).strip()
    folded = _fold(value)
    if _ROSTER_GAP.fullmatch(folded):
        return True
    # Antes de outro nome, o convidado externo precisa terminar em separador.
    if not tail and not re.search(r'(?:[,;/|&+]|\be)$', folded):
        return False
    if folded.endswith(('.', '!', '?')):
        return False
    entries = re.split(r'[,;/|&+]|\be\b', value)
    for entry in entries:
        entry = entry.strip(' \t[]-*•')
        if not entry:
            continue
        words = entry.split()
        if len(words) > 8 or any(_fold(word) in _PROSE_WORDS for word in words):
            return False
        for index, word in enumerate(words):
            if _fold(word) in _NAME_PARTICLES and 0 < index < len(words) - 1:
                continue
            if (not word[0].isupper() or
                    not re.fullmatch(r'[^\W\d_]+(?:[-\'’][^\W\d_]+)*', word)):
                return False
    return True


def _names(people):
    claims, spellings, official = defaultdict(set), {}, set()
    for person in people:
        try:
            pid = int(person['id'])
        except (KeyError, TypeError, ValueError):
            continue
        full_name = person.get('full_name') or ''
        if not isinstance(full_name, str) or not full_name.strip():
            continue
        full_name = ' '.join(full_name.split())
        aliases = person.get('transcript_aliases') or []
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except (TypeError, ValueError):
                aliases = []
        if not isinstance(aliases, list):
            aliases = []
        words = full_name.split()
        candidates = [full_name, person.get('preferred_name'), words[0]]
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                continue
            alias = ' '.join(alias.split())
            candidates.append(alias)
            # Mantém as mesmas variantes compostas da correção de transcrição.
            if _fold(alias.split()[0]) != _fold(words[0]):
                candidates.extend(' '.join(words[:i] + [alias])
                                  for i in range(1, len(words)))
        official.add(_fold(full_name))
        for name in candidates:
            if not isinstance(name, str):
                continue
            name = ' '.join(name.split())
            if not name or not any(c.isalpha() for c in name):
                continue
            key = _fold(name)
            if len(key) < 3 and name != full_name:
                continue
            claims[key].add(pid)
            spellings.setdefault(key, name)
    return claims, spellings, official


def _address_ranges(line):
    """Protege tokens completos sem regex de e-mail quadrática em textos longos."""
    result = []
    for match in re.finditer(r'\S+', line):
        token = match.group()
        if ('@' in token or '://' in token or token.startswith('www.') or
                ('.' in token and re.search(r'(?<![\w-])[\w-]+\.[a-z]{2,}(?:[^\w-]|$)', token))):
            result.append((match.start(), match.end()))
    return result


def _roster_signals(line, matches, start, original, offsets):
    """Só aceita uma lista de nomes; interrompe ao começar o conteúdo da reunião."""
    cursor = start
    result = set()
    starts, ends = offsets

    def original_slice(left, right):
        if left == right:
            return ''
        return original[starts[left]:ends[right - 1]]

    for index, match in enumerate(matches):
        if match.start() < start:
            continue
        if not _roster_gap(original_slice(cursor, match.start())):
            return result, False
        following = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        suffix = line[match.end():min(following, match.end() + 160)]
        if not _ABSENT.match(suffix):
            result.add(index)
        cursor = match.end()
    return result, _roster_gap(original_slice(cursor, len(line)), tail=True)


def detect_meeting_participants(transcript_raw, notes, people):
    """Retorna IDs e nomes ambíguos a partir de sinais de fala ou presença.

    Aceita nomes completos, nomes preferidos, primeiro nome único e variações
    cadastradas. Usa fronteiras de palavras, tolera acentos e espaços horizontais
    e prioriza a variante mais longa. Não cria perfis nem atribui responsabilidades.
    """
    claims, spellings, official = _names(people)
    if not claims:
        return {'participant_ids': [], 'ambiguous_names': []}
    pattern = _name_pattern(claims)
    found, ambiguous = set(), set()
    for source in (transcript_raw, notes):
        if not isinstance(source, str) or not source:
            continue
        # Homogeneíza somente espaços horizontais; nomes nunca cruzam linhas.
        originals = re.sub(r'[^\S\r\n]+', ' ', source).splitlines()
        lines = [_fold(line) for line in originals]
        roster = False
        for lineno, line in enumerate(lines):
            if not line.strip():
                continue
            all_matches = list(pattern.finditer(line))
            protected = _address_ranges(line) if all_matches else []
            matches, protected_index = [], 0
            for match in all_matches:
                while protected_index < len(protected) and protected[protected_index][1] <= match.start():
                    protected_index += 1
                if protected_index < len(protected) and protected[protected_index][0] < match.end():
                    continue
                matches.append(match)
            headings = list(_ATTENDANCE.finditer(line))
            signals = set()
            offsets = _fold_with_offsets(originals[lineno])[1:] if roster or headings else None
            if roster:
                found_here, roster = _roster_signals(line, matches, 0, originals[lineno], offsets)
                signals |= found_here
            for heading in headings:
                found_here, roster = _roster_signals(line, matches, heading.end(), originals[lineno], offsets)
                signals |= found_here
            for index, match in enumerate(matches):
                key = ' '.join(match.group().split())
                prefix = line[:match.start()] if match.start() < 120 else None
                suffix = line[match.end():match.end() + 160]
                speaker_prefix = prefix is not None and _SPEAKER_PREFIX.fullmatch(prefix)
                speaker = speaker_prefix and _SPEAKER_SUFFIX.match(suffix)
                if speaker and _ABSENT.match(suffix[speaker.end():]):
                    speaker = False
                standalone = speaker_prefix and _STANDALONE_SUFFIX.fullmatch(line[match.end():])
                timestamp_next = (lineno + 1 < len(lines) and _TIME_LINE.fullmatch(lines[lineno + 1]))
                reported = _REPORTED_MENTION.search(line[max(0, match.start() - 80):match.start()])
                speech = _SPEECH.match(suffix) and not reported
                if (index not in signals and not speaker and not speech and
                        not (standalone and (key in official or timestamp_next))):
                    continue
                if len(claims[key]) == 1:
                    found.update(claims[key])
                else:
                    ambiguous.add(spellings[key])
    return {'participant_ids': sorted(found),
            'ambiguous_names': sorted(ambiguous, key=lambda name: (_fold(name), name))}
