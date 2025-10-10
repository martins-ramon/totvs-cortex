# FeedbackAI - Sistema de Feedback Assistido por IA

Sistema revolucionário de feedback que transforma gestores em "gestores aumentados" com produtividade 10x através de insights inteligentes gerados por IA.

## 🚀 Funcionalidades

- **Autenticação Completa**: Sistema de login/registro para gestores
- **Gestão de Funcionários**: Cadastro e gerenciamento de funcionários vinculados ao gestor
- **Feedback Avançado**: Formulário com campos específicos e vetorização automática para RAG
- **Dashboard Inteligente**: Insights automáticos sobre:
  - Pontos de desenvolvimento
  - Fortalezas da equipe
  - Riscos de saída (turnover)
  - Situações que requerem atenção

## 📊 Tecnologias

- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Backend**: Python Flask
- **Database**: PostgreSQL com pgvector
- **IA**: OpenAI (embeddings + GPT-4)

## ⚙️ Configuração

### Opção 1: Usar Database do Replit (Recomendado)

1. No painel do Replit, vá em "Tools" → "Database"
2. Crie um novo PostgreSQL database
3. A variável `DATABASE_URL` será automaticamente configurada

### Opção 2: Usar Supabase

Se você preferir usar Supabase, você precisa do **Transaction Pooler** (porta 6543):

1. Vá para [Supabase Dashboard](https://supabase.com/dashboard/projects)
2. Selecione seu projeto
3. Clique em "Connect" no topo
4. Escolha "Transaction pooler" (não "Direct connection")
5. Copie a URI que usa porta **6543** (não 5432)
6. Adicione como secret `DATABASE_URL`

**IMPORTANTE**: A porta deve ser 6543 (pooler), não 5432 (direct). Conexões diretas (porta 5432) podem não funcionar no Replit.

### Secrets Necessários

- `DATABASE_URL`: String de conexão PostgreSQL
- `OPENAI_API_KEY`: Chave da API OpenAI
- `SESSION_SECRET`: (opcional) Chave secreta para sessões

## 🎨 Design

Interface inspirada em Lattice e 15Five com:
- Cores: Indigo (#6366F1), Verde (#10B981), Amarelo (#F59E0B)
- Design moderno com cards e animações suaves
- Layout responsivo

## 📝 Como Usar

1. **Registre-se** com email, nome e empresa
2. **Adicione funcionários** na seção "Funcionários"
3. **Cadastre feedbacks** com insights completos
4. **Veja o Dashboard** com análises automáticas geradas por IA

## 🔒 Segurança

- Senhas hash com SHA-256
- Sessões seguras com Flask
- Variáveis de ambiente para credenciais
