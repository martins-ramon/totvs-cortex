# 🔧 Instruções de Configuração - FeedbackAI

## ⚠️ Problema de Conexão com Supabase Detectado

Sua conexão Supabase está usando **porta 5432** (conexão direta), que pode não funcionar no Replit devido a restrições de firewall.

## ✅ Solução: Use Transaction Pooler (Porta 6543)

### Passo a Passo para Corrigir:

1. **Acesse seu Supabase Dashboard**
   - Vá para: https://supabase.com/dashboard/projects

2. **Selecione seu Projeto**

3. **Clique no botão "Connect"** (topo da página)

4. **IMPORTANTE: Selecione "Transaction pooler"**
   - NÃO use "Direct connection"
   - Transaction pooler usa porta 6543 ✅
   - Direct connection usa porta 5432 ❌ (não funciona no Replit)

5. **Copie a Connection String**
   - Escolha "URI" 
   - Vai parecer com: `postgresql://postgres.[project]:[password]@aws-0-us-east-1.pooler.supabase.com:6543/postgres`
   - Note a porta **6543** na URL

6. **Atualize o Secret DATABASE_URL**
   - Vá em Tools → Secrets no Replit
   - Edite DATABASE_URL com a nova connection string
   - Certifique-se de substituir `[YOUR-PASSWORD]` pela sua senha real

7. **Reinicie o servidor**
   - Clique em "Stop" e depois "Run" novamente

## 🔄 Alternativa: Usar Database do Replit

Se você preferir não usar Supabase:

1. No painel do Replit, vá em **Tools → Database**
2. Clique em **Create a PostgreSQL Database**
3. O Replit vai configurar automaticamente o DATABASE_URL
4. Reinicie o servidor

Esta opção é mais simples e funciona imediatamente!

## 📋 Verificar Configuração

Após atualizar, verifique se:
- ✅ DATABASE_URL contém porta **6543** (pooler) ou é do Replit
- ✅ OPENAI_API_KEY está configurado
- ✅ Servidor inicia sem erros de conexão

## 🆘 Ainda com Problemas?

Se continuar com erros:
1. Verifique se a senha na connection string está correta
2. Certifique-se de que não há espaços extras na URL
3. Confirme que o projeto Supabase está ativo
