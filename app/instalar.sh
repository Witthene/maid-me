#!/usr/bin/env bash
# Maid Me installer for Linux. Run through "Instalar Maid Me (Linux).sh" in the folder above.
# Everything stays inside this folder (.venv) plus a menu entry; nothing runs at startup.
set -e
cd "$(dirname "$(readlink -f "$0")")"

step() { printf '\n\033[35m[%s/4] %s\033[0m\n' "$1" "$2"; }

clear || true
printf '\033[35m  Maid Me - instalação\033[0m\n'
echo '  Assistente de voz. Código aberto, licença MIT.'
echo '  Tudo fica dentro desta pasta. Para desinstalar, apague a pasta e o atalho do menu.'

step 1 'Verificando o Python e os pacotes do sistema'
PY=""
for v in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$v" >/dev/null && "$v" -c 'import sys; exit(sys.version_info < (3, 11))' 2>/dev/null; then PY="$v"; break; fi
done
# system pieces: venv, the mic library, Qt's X11 cursor lib, and the tools for window/idle info and sounds
if command -v apt-get >/dev/null; then
    PKGS="python3-venv libportaudio2 libxcb-cursor0 xdotool x11-utils xprintidle pulseaudio-utils fonts-dejavu-core fonts-noto-color-emoji"
    [ -z "$PY" ] && PKGS="python3 $PKGS"
    echo "Pode pedir a sua senha pra instalar: $PKGS"
    sudo apt-get install -y $PKGS
elif command -v dnf >/dev/null; then
    sudo dnf install -y python3 portaudio xcb-util-cursor xdotool xprop xprintidle pulseaudio-utils dejavu-sans-fonts google-noto-emoji-color-fonts
elif command -v pacman >/dev/null; then
    sudo pacman -S --needed --noconfirm python portaudio xcb-util-cursor xdotool xorg-xprop xprintidle libpulse ttf-dejavu noto-fonts-emoji
else
    echo 'Distribuição não reconhecida: instale Python 3.11+, PortAudio, xdotool, xprop e xprintidle pelo seu gerenciador.'
fi
[ -z "$PY" ] && PY=python3
echo "Usando $($PY --version)"

step 2 'Criando o ambiente isolado do programa'
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check

step 3 'Baixando as bibliotecas do PyPI (pode levar alguns minutos)'
.venv/bin/python -m pip install -r requirements.txt --disable-pip-version-check
if command -v nvidia-smi >/dev/null; then
    echo 'Placa NVIDIA encontrada: instalando a aceleração por GPU.'
    .venv/bin/python -m pip install -r requirements-gpu.txt --disable-pip-version-check
fi

step 4 'Criando o atalho no menu'
chmod +x iniciar.sh
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/maid-me.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Maid Me
Comment=Assistente de voz
Exec="$PWD/iniciar.sh"
Icon=$PWD/assets/logo.png
Terminal=false
Categories=Utility;
EOF

if [ "${XDG_SESSION_TYPE:-x11}" = "wayland" ]; then
    printf '\n\033[33mAviso: você está no Wayland. A assistente funciona, mas os efeitos em cima das janelas e os\n'
    printf 'comentários sobre o programa em uso precisam de uma sessão X11 (na tela de login: "Xorg").\033[0m\n'
fi
printf '\n\033[32mInstalação concluída. Abrindo o Maid Me...\033[0m\n'
nohup ./iniciar.sh >/dev/null 2>&1 &
