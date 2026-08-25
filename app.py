from cortex import create_app

# Instância no nível do módulo para o Gunicorn (`gunicorn app:app`)
app = create_app()

if __name__ == '__main__':
    # Servidor de desenvolvimento
    app.run(host='0.0.0.0', port=5000, debug=True)
