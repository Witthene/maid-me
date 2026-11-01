#!/usr/bin/env bash
# Maid Me for Linux: run with   bash "Instalar Maid Me (Linux).sh"
cd "$(dirname "$(readlink -f "$0")")"
bash app/instalar.sh || { echo; echo 'A instalação não terminou. Veja a mensagem acima.'; read -r -p 'Enter pra fechar'; }
