"""Decides which tool calls are dangerous enough to ask the user out loud.

Everything else is approved automatically: the user trusts the assistant, except for things that
can break Windows or wipe a whole drive / the user's profile.
"""
import os
import re

HOME = os.path.expanduser("~").lower().replace("/", "\\")

# Commands that are dangerous no matter what they touch
ALWAYS = [
    (r"\bformat(\.com)?\s+[a-z]:", "formatar um disco"),
    (r"format-volume|clear-disk|initialize-disk|remove-partition|\bdiskpart\b", "mexer nas partições do disco"),
    (r"\bbcdedit\b|\bbootrec\b", "mexer na inicialização do Windows"),
    (r"vssadmin\s+delete|wbadmin\s+delete", "apagar os backups do sistema"),
    (r"\bcipher\s+/w|\bsdelete\b", "apagar dados de forma irrecuperável"),
    (r"reg(\.exe)?\s+delete\s+(hklm|hkey_local_machine)|remove-item(property)?\b[^;|]*hklm:", "apagar coisas do registro do sistema"),
    (r"\btakeown\b|\bicacls\b[^;|]*(windows|system32|program files)", "mudar as permissões de arquivos do sistema"),
    (r"set-mppreference[^;|]*disable|netsh\s+advfirewall[^;|]*\boff\b|set-netfirewallprofile[^;|]*false",
     "desligar o antivírus ou o firewall"),
    (r"\bsc(\.exe)?\s+delete\b|remove-service", "remover um serviço do Windows"),
    (r"(stop-process|taskkill)[^;|]*(csrss|lsass|winlogon|wininit|smss|services|svchost)", "matar um processo essencial do Windows"),
    (r"\bshutdown\b|restart-computer|stop-computer", "desligar ou reiniciar o PC"),
]

DESTRUCTIVE = r"(remove-item|\bri\b|\brm\b|\bdel\b|\berase\b|\brd\b|\brmdir\b|move-item|\bmv\b|\bmove\b|rename-item|clear-content|set-content|out-file|copy-item|\bcopy\b|>)"
SYSTEM_PATHS = r"(c:\\windows|system32|syswow64|\$env:(systemroot|windir)|%(systemroot|windir)%|c:\\program files|\$env:programfiles|c:\\programdata|c:\\boot|c:\\recovery)"
DELETE = r"(remove-item|\bri\b|\brm\b|\bdel\b|\berase\b|\brd\b|\brmdir\b)"
END = r"['\"]?\\?['\"]?(\s|$|;|\|)"
WIPE_TARGETS = [r"[a-z]:", re.escape(HOME), r"\$env:userprofile", r"~", r"c:\\users"]


def danger(tool_name, tool_input):
    """Reason (pt-BR, infinitive) why this call needs a spoken yes, or None if it is safe."""
    if tool_name in ("PowerShell", "Bash"):
        cmd = (tool_input.get("command") or "").lower().replace("/", "\\")
        for pattern, reason in ALWAYS:
            if re.search(pattern, cmd):
                return reason
        if re.search(DESTRUCTIVE, cmd) and re.search(SYSTEM_PATHS, cmd):
            return "mexer em arquivos do Windows"
        for target in WIPE_TARGETS:
            if re.search(DELETE + r"[^;|]*\s['\"]?" + target + END, cmd):
                return "apagar uma pasta inteira de nível mais alto, tipo um disco ou a sua pasta de usuário"
        return None
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        path = (tool_input.get("file_path") or tool_input.get("notebook_path") or "").lower().replace("/", "\\")
        if re.search(SYSTEM_PATHS, path):
            return "mexer em arquivos do Windows"
    return None
