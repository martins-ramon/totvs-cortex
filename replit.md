# FeedbackAI - Sistema de Feedback Assistido por IA

## 📋 Visão Geral do Projeto

Sistema revolucionário de feedback que transforma gestores em "gestores aumentados" com produtividade 10x através de insights inteligentes gerados por IA usando análise de dados históricos e vetorização de feedbacks.

## 🎯 Funcionalidades Principais (MVP)

### ✅ Implementado

1. **Sistema de Autenticação**
   - Login e registro de gestores
   - Dados de empresa vinculados ao gestor
   - Sessões seguras com Flask

2. **Gestão de Funcionários**
   - CRUD completo de funcionários
   - Vinculação ao gestor logado
   - Campos: nome, email, cargo, departamento

3. **Sistema Avançado de Feedback**
   - Campos específicos:
     - Feedback ao funcionário
     - Feedback ao gestor
     - Expectativas sobre a empresa
     - Expectativas sobre o gestor
   - **Vetorização automática** com OpenAI embeddings para RAG
   - Armazenamento em PostgreSQL com pgvector

4. **Dashboard Inteligente**
   - Exibição do último feedback de cada funcionário
   - **Geração automática de insights com IA**:
     - Pontos de desenvolvimento
     - Fortalezas identificadas
     - Nível de risco de saída (baixo/médio/alto)
     - Situações que requerem atenção do gestor

5. **Interface Profissional**
   - Design inspirado em Lattice/15Five
   - Paleta de cores: #6366F1 (indigo), #10B981 (verde), #F59E0B (amarelo)
   - Layout responsivo com sidebar
   - Animações suaves e transições fluidas
   - Cards de insights bem estruturados

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
- ✅ Código implementado
- ✅ Dependências instaladas
- ⚠️ **Problema de conexão com database detectado**

### Problema Identificado
O DATABASE_URL atual usa porta 5432 (conexão direta Supabase) que pode não funcionar no Replit.

### Soluções Disponíveis

**Opção 1 (Recomendada)**: Use Transaction Pooler do Supabase
- Porta 6543 ao invés de 5432
- Ver instruções em `SETUP_INSTRUCTIONS.md`

**Opção 2 (Alternativa)**: Use Replit Database
- Criar PostgreSQL no painel Tools → Database
- Configuração automática

## 🎨 Arquitetura de Dados

### Tabelas

**managers**
- id, email, password_hash, name, company_name, created_at

**employees**
- id, manager_id (FK), name, email, position, department, created_at

**feedbacks**
- id, employee_id (FK), manager_id (FK)
- feedback_to_employee, feedback_to_manager
- expectations_company, expectations_manager
- **embedding (vector 1536)** ← Vetorização para RAG
- created_at

### Fluxo de Insights com IA

1. **Entrada**: Feedback cadastrado
2. **Vetorização**: OpenAI embeddings (text-embedding-3-small)
3. **Armazenamento**: Vector no PostgreSQL/pgvector
4. **Análise**: GPT-4o-mini analisa histórico de feedbacks
5. **Output**: JSON com insights estruturados

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
