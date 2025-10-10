# FeedbackAI - Sistema de Feedback Assistido por IA

## 📋 Visão Geral do Projeto

Sistema revolucionário de feedback com modelo self-service onde usuários se cadastram e escolhem seus gestores. IA gera insights inteligentes usando análise de dados históricos e vetorização de feedbacks.

## 🎯 Funcionalidades Principais (MVP)

### ✅ Implementado

1. **Sistema de Autenticação Self-Service**
   - Login e registro aberto para todos os usuários
   - Dados completos: nome, email, empresa, telefone
   - Seleção opcional de gestor no próprio perfil
   - Sessões seguras com Flask

2. **Gestão de Perfil (Minha Conta)**
   - Visualização e edição de dados pessoais
   - Seleção/alteração de gestor de forma autônoma
   - Lista de todos os usuários disponíveis para seleção
   - Campos: nome, email, empresa, telefone, gestor

3. **Sistema Avançado de Feedback**
   - Campos específicos:
     - Feedback ao usuário
     - Feedback ao gestor
     - Expectativas sobre a empresa
     - Expectativas sobre o gestor
   - **Vetorização automática** com OpenAI embeddings para RAG
   - Armazenamento em PostgreSQL com pgvector
   - Data do feedback configurável

4. **Dashboard Inteligente**
   - Exibição do último feedback de cada usuário gerenciado
   - Listagem automática de quem escolheu você como gestor
   - **Geração automática de insights com IA**:
     - Resumo do feedback
     - Pontos de desenvolvimento
     - Fortalezas identificadas
     - Nível de risco de saída (baixo/médio/alto)
     - Ações pendentes sugeridas

5. **Interface Profissional**
   - Design inspirado em Lattice/15Five
   - Paleta de cores: #6366F1 (indigo), #10B981 (verde), #F59E0B (amarelo)
   - Layout responsivo com sidebar
   - Animações suaves e transições fluidas
   - Cards de insights com expand/collapse
   - Toast notifications para feedback do sistema

## 🛠 Stack Tecnológica

### Frontend
- HTML5 + CSS3
- Vanilla JavaScript (ES6+)
- Google Fonts (Inter)
- Design responsivo

### Backend
- Python 3.11
- Flask (web framework)
- SQLAlchemy (ORM)
- Flask-CORS

### Database
- PostgreSQL com pgvector extension
- Supabase ou Replit Database
- Vetores de 1536 dimensões (OpenAI embeddings)

### IA/ML
- OpenAI API:
  - text-embedding-3-small (vetorização)
  - gpt-4o-mini (geração de insights)

## 📁 Estrutura do Projeto

```
/
├── app.py                      # Backend Flask principal
├── static/
│   ├── index.html             # Interface principal
│   ├── styles.css             # Estilos visuais
│   └── app.js                 # Lógica frontend
├── README.md                   # Documentação geral
├── SETUP_INSTRUCTIONS.md      # Instruções de configuração
└── replit.md                  # Este arquivo
```

## 🔐 Variáveis de Ambiente Necessárias

- `DATABASE_URL`: Connection string PostgreSQL
  - **Supabase**: Usar Transaction Pooler (porta 6543)
  - **Replit**: Configurado automaticamente
- `OPENAI_API_KEY`: Chave API da OpenAI
- `SESSION_SECRET`: (opcional) Chave para sessões Flask

## ⚙️ Status de Configuração

### Atual
- ✅ Código implementado e refatorado (Outubro 2025)
- ✅ Dependências instaladas
- ✅ Arquitetura self-service implementada
- ✅ Database PostgreSQL com pgvector configurado

### Importante
- Usar Transaction Pooler do Supabase (porta 6543) ou Replit Database
- Ver instruções em `SETUP_INSTRUCTIONS.md`

## 🎨 Arquitetura de Dados

### Modelo Unificado (Outubro 2025)

**users** (tabela principal única)
- id (PK)
- email (unique)
- password_hash
- name
- company
- phone (opcional)
- manager_id (FK self-referencing, opcional)
- created_at

**feedbacks**
- id (PK)
- user_id (FK → users.id) - quem recebe o feedback
- author_id (FK → users.id) - quem escreve o feedback
- feedback_to_user (text)
- feedback_to_manager (text)
- expectations_company (text)
- expectations_manager (text)
- feedback_date (date)
- **embedding (vector 1536)** ← Vetorização para RAG
- created_at

### Fluxo Self-Service

1. **Registro**: Usuário se cadastra com dados básicos
2. **Seleção de Gestor**: Usuário escolhe gestor em "Minha Conta"
3. **Feedback**: Gestor vê automaticamente quem o escolheu e dá feedbacks
4. **Dashboard**: Gestor vê insights de todos os seus "gerenciados"

### Fluxo de Insights com IA

1. **Entrada**: Feedback cadastrado pelo gestor
2. **Vetorização**: OpenAI embeddings (text-embedding-3-small)
3. **Armazenamento**: Vector no PostgreSQL/pgvector
4. **Análise**: GPT-4o-mini analisa histórico de feedbacks do usuário
5. **Output**: JSON estruturado em português com insights acionáveis

## 🚀 Próximas Fases (Planejadas)

1. **Visualizações Avançadas**
   - Timeline de feedbacks
   - Gráficos de tendências
   - Comparações entre membros da equipe

2. **Análise em Lote**
   - Processamento de múltiplos feedbacks
   - Insights comparativos

3. **Personalização**
   - Templates customizáveis
   - Treinamento específico por gestor

4. **Notificações**
   - Alertas para insights críticos
   - Action items automáticos

5. **Relatórios**
   - Exportação PDF/Excel
   - Analytics de performance

## 🔄 Workflow Configurado

- **Nome**: Server
- **Comando**: `python app.py`
- **Porta**: 5000
- **Tipo**: webview

## 📝 Convenções de Código

- Backend: PEP 8 (Python)
- Frontend: ES6+ padrão
- Commits: Mensagens descritivas
- Segurança: Nunca commitar secrets

## 🐛 Debugging

- Logs do servidor: Verificar console do workflow
- Erros frontend: Browser dev tools
- Database: Verificar conexão e tabelas criadas

## 👤 Preferências do Usuário

- Idioma: Português (Brasil)
- Interface inspirada em: Lattice, 15Five
- Stack específica: Python + Vanilla JS (sem TypeScript)
- Database: Supabase preferido
