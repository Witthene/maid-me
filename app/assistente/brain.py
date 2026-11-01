"""The assistant's brain.

Two kinds, both putting the same events on the queue (Claude Code's stream-json format):
- ClaudeSession: one long-lived Claude Code process, signed in with the user's Claude plan.
- ApiSession: any chat API with tool calling (Anthropic, Gemini, OpenAI, OpenRouter, Groq,
  DeepSeek, Ollama...), running the PC tools itself.
"""
import html
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import uuid

import requests

# name, API flavor, base URL, suggested model ("Listar modelos" in the configurator shows the rest)
PROVIDERS = {
    "claude-code": ("Claude (assinatura, via Claude Code)", "claude-code", "", ""),
    "anthropic": ("Claude (chave de API da Anthropic)", "anthropic", "https://api.anthropic.com/v1", "claude-haiku-5-5"),
    "gemini": ("Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    "openai": ("OpenAI (ChatGPT)", "openai", "https://api.openai.com/v1", "gpt-4.1-mini"),
    "openrouter": ("OpenRouter (vários modelos)", "openai", "https://openrouter.ai/api/v1", ""),
    "groq": ("Groq", "openai", "https://api.groq.com/openai/v1", ""),
    "deepseek": ("DeepSeek", "openai", "https://api.deepseek.com/v1", "deepseek-chat"),
    "ollama": ("Ollama (local, grátis)", "openai", "http://localhost:11434/v1", ""),
    "custom": ("Outro compatível com OpenAI", "openai", "", ""),
}


# --- Claude Code -------------------------------------------------------------
# Only what the assistant needs: every extra tool, MCP server or command adds tokens to every turn
TOOLS = "PowerShell,Read,Write,Edit,WebSearch,WebFetch"
LEAN_FLAGS = ["--tools", TOOLS, "--strict-mcp-config", "--disable-slash-commands", "--no-chrome",
              "--no-session-persistence"]


def claude_exe():
    exe = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "@anthropic-ai",
                       "claude-code", "bin", "claude.exe")
    return exe if os.path.exists(exe) else shutil.which("claude")


class ClaudeSession:
    def __init__(self, persona, cwd, events, model=None, effort="low"):
        exe = claude_exe()
        if not exe:
            raise SystemExit("Claude Code nao encontrado. Veja o passo do Claude no GUIA.html.")
        args = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--permission-prompt-tool", "stdio", "--append-system-prompt", persona,
                "--effort", effort, *LEAN_FLAGS]
        if model:
            args += ["--model", model]
        self._events = events
        self.proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def send(self, content):
        self._write({"type": "user", "message": {"role": "user", "content": content}})

    def answer_permission(self, request, allow):
        if allow:
            decision = {"behavior": "allow", "updatedInput": request["request"].get("input", {})}
        else:
            decision = {"behavior": "deny", "message": "O usuario nao autorizou por voz. Nao tente outro caminho."}
        self._write({"type": "control_response", "response": {
            "subtype": "success", "request_id": request["request_id"], "response": decision}})

    def reject_request(self, request):
        self._write({"type": "control_response", "response": {
            "subtype": "error", "request_id": request["request_id"], "error": "nao suportado"}})

    def interrupt(self):
        self._write({"type": "control_request", "request_id": str(uuid.uuid4()),
                     "request": {"subtype": "interrupt"}})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def _write(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read_stdout(self):
        # events carry the session, so messages from a session that was replaced can be ignored
        for line in self.proc.stdout:
            try:
                self._events.put(("claude", (self, json.loads(line))))
            except ValueError:
                pass
        self._events.put(("claude_exit", (self, self.proc.wait())))

    def _read_stderr(self):
        for line in self.proc.stderr:
            if line.strip():
                print(f"[claude] {line.rstrip()}")


# --- PC tools for the API brains ---------------------------------------------
# Same names as Claude Code's tools, so the safety check and the spoken questions work for both
TOOL_SPECS = [
    ("PowerShell", "Roda um comando no PowerShell do Windows do usuário e devolve a saída.",
     {"command": ("string", "O comando PowerShell."),
      "description": ("string", "O que o comando faz, em português, começando com verbo no infinitivo.")}),
    ("Read", "Lê um arquivo de texto do PC.", {"file_path": ("string", "Caminho completo do arquivo.")}),
    ("Write", "Cria ou substitui um arquivo de texto.",
     {"file_path": ("string", "Caminho completo do arquivo."), "content": ("string", "Conteúdo completo.")}),
    ("WebSearch", "Pesquisa na internet e devolve os primeiros resultados.", {"query": ("string", "O que pesquisar.")}),
    ("WebFetch", "Abre uma página da internet e devolve o texto dela.", {"url": ("string", "Endereço da página.")}),
]
OUTPUT_LIMIT = 8000
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def _schema(params):
    return {"type": "object", "required": list(params),
            "properties": {k: {"type": t, "description": d} for k, (t, d) in params.items()}}


def _clip(text):
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + "\n[...cortado]"


def _page_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    return " ".join(html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw)).split())


def run_tool(name, args, cwd):
    try:
        if name == "PowerShell":
            cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + args.get("command", "")
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], cwd=cwd,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
            return _clip(f"(código de saída {r.returncode})\n{out}" if r.returncode else out or "(sem saída)")
        if name == "Read":
            with open(args["file_path"], encoding="utf-8", errors="replace") as f:
                return _clip(f.read())
        if name == "Write":
            os.makedirs(os.path.dirname(os.path.abspath(args["file_path"])), exist_ok=True)
            with open(args["file_path"], "w", encoding="utf-8") as f:
                f.write(args.get("content", ""))
            return "Arquivo salvo."
        if name == "WebSearch":
            r = requests.post("https://html.duckduckgo.com/html/", data={"q": args["query"]}, headers=UA, timeout=20)
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.S)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
            results = [f"- {_page_text(t)}: {_page_text(s)}" for t, s in zip(titles, snippets)][:8]
            return "\n".join(results) or "Nenhum resultado."
        if name == "WebFetch":
            r = requests.get(args["url"], headers=UA, timeout=20)
            return _clip(_page_text(r.text))
        return f"Ferramenta desconhecida: {name}"
    except subprocess.TimeoutExpired:
        return "O comando demorou demais e foi cancelado."
    except Exception as e:
        return f"Erro: {e}"


# --- chat APIs -----------------------------------------------------------------
class Anthropic:
    def __init__(self, api_key, model, base_url, system):
        self.url = (base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
        self.headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        self.model, self.system = model, system
        self.tools = [{"name": n, "description": d, "input_schema": _schema(p)} for n, d, p in TOOL_SPECS]

    def user(self, content):
        return {"role": "user", "content": content}

    def call(self, history):
        body = {"model": self.model, "max_tokens": 2048, "system": self.system, "messages": history}
        if self.tools:
            body["tools"] = self.tools
        r = requests.post(self.url, headers=self.headers, timeout=120, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        content = r.json()["content"]
        text = "".join(c["text"] for c in content if c["type"] == "text")
        calls = [(c["id"], c["name"], c["input"]) for c in content if c["type"] == "tool_use"]
        return {"role": "assistant", "content": content}, text, calls

    def results(self, results, extra_text):
        blocks = [{"type": "tool_result", "tool_use_id": i, "content": out} for i, out in results]
        blocks += [{"type": "text", "text": t} for t in extra_text]
        return [{"role": "user", "content": blocks}]


class OpenAICompatible:
    def __init__(self, api_key, model, base_url, system):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key or 'sem-chave'}"}
        self.model, self.system = model, system
        self.tools = [{"type": "function", "function": {"name": n, "description": d, "parameters": _schema(p)}}
                      for n, d, p in TOOL_SPECS]

    def user(self, content):
        if isinstance(content, list):  # Anthropic-style blocks from the screen watcher
            content = [{"type": "image_url", "image_url": {"url": f"data:{c['source']['media_type']};base64,"
                                                          f"{c['source']['data']}"}}
                       if c.get("type") == "image" else c for c in content]
        return {"role": "user", "content": content}

    def call(self, history):
        body = {"model": self.model, "messages": [{"role": "system", "content": self.system}, *history]}
        if self.tools:
            body["tools"] = self.tools
        r = requests.post(self.url, headers=self.headers, timeout=120, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        msg = r.json()["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append((c["id"], c["function"]["name"], args))
        entry = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            entry["tool_calls"] = msg["tool_calls"]
        return entry, msg.get("content") or "", calls

    def results(self, results, extra_text):
        out = [{"role": "tool", "tool_call_id": i, "content": o} for i, o in results]
        return out + [{"role": "user", "content": t} for t in extra_text]


class ApiSession:
    MAX_STEPS = 25
    PERMISSION_WAIT = 120

    def __init__(self, persona, cwd, events, provider, api_key, model, base_url=None):
        _, flavor, default_url, default_model = PROVIDERS[provider]
        api = Anthropic if flavor == "anthropic" else OpenAICompatible
        self.api = api(api_key, model or default_model, base_url or default_url, persona)
        self._cwd = cwd
        self._events = events
        self._inbox = queue.Queue()
        self._history = []
        self._answers = {}  # request_id -> (Event, [allowed])
        self._interrupted = threading.Event()
        self._closed = False
        threading.Thread(target=self._worker, daemon=True).start()

    def send(self, content):
        self._inbox.put(content)

    def answer_permission(self, request, allow):
        slot = self._answers.pop(request["request_id"], None)
        if slot:
            slot[1].append(allow)
            slot[0].set()

    def reject_request(self, request):
        pass

    def interrupt(self):
        self._interrupted.set()
        for event, _ in list(self._answers.values()):
            event.set()

    def close(self):
        self._closed = True
        self.interrupt()
        self._inbox.put(None)

    def _emit(self, msg):
        if not self._closed:
            self._events.put(("claude", (self, msg)))

    def _worker(self):
        while True:
            content = self._inbox.get()
            if content is None:
                return
            self._interrupted.clear()
            self._history.append(self.api.user(content))
            try:
                self._turn()
                self._emit({"type": "result", "is_error": False})
            except Exception as e:
                print(f"[cerebro] erro: {e}")
                self._history.append({"role": "assistant", "content": "(erro)"})
                self._emit({"type": "assistant", "message": {"content": [{"type": "text", "text":
                            "[triste] Meu cérebro deu erro. Confere a chave e o modelo no configurador."}]}})
                self._emit({"type": "result", "is_error": True, "subtype": "api", "result": str(e)})

    def _turn(self):
        for _ in range(self.MAX_STEPS):
            if self._interrupted.is_set():
                return
            entry, text, calls = self.api.call(self._history)
            self._history.append(entry)
            blocks = [{"type": "text", "text": text}] if text.strip() else []
            blocks += [{"type": "tool_use", "name": n, "input": a} for _, n, a in calls]
            if blocks:
                self._emit({"type": "assistant", "message": {"content": blocks}})
            if not calls:
                return
            results = [(i, self._use_tool(n, a)) for i, n, a in calls]
            # messages that arrived mid-turn go along with the tool results
            extra = []
            while not self._inbox.empty():
                item = self._inbox.get()
                if item is None:
                    self._inbox.put(None)
                    break
                extra.append(item if isinstance(item, str) else "(imagem da tela ignorada)")
            self._history += self.api.results(results, extra)

    def _use_tool(self, name, args):
        if self._interrupted.is_set():
            return "Interrompido pelo usuário."
        request_id = str(uuid.uuid4())
        event, answer = threading.Event(), []
        self._answers[request_id] = (event, answer)
        # the main loop approves safe calls right away and asks out loud about dangerous ones
        self._emit({"type": "control_request", "request_id": request_id,
                    "request": {"subtype": "can_use_tool", "tool_name": name, "input": args}})
        event.wait(self.PERMISSION_WAIT)
        if not answer or not answer[0]:
            return "O usuário não autorizou por voz. Não tente outro caminho."
        return run_tool(name, args, self._cwd)


# --- helpers for the configurator ---------------------------------------------
def list_models(provider, api_key, base_url=None):
    _, flavor, default_url, _ = PROVIDERS[provider]
    url = (base_url or default_url).rstrip("/") + "/models"
    if flavor == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        headers = {"Authorization": f"Bearer {api_key or 'sem-chave'}"}
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code} {r.text[:200]}")
    ids = [m["id"] for m in r.json().get("data", [])]
    return sorted(i.removeprefix("models/") for i in ids)


def quick_test(provider, api_key, model, base_url=None):
    """One short reply, to check key and model. Returns the text or raises."""
    if provider == "claude-code":
        exe = claude_exe()
        if not exe:
            raise RuntimeError("Claude Code não está instalado.")
        args = [exe, "-p", "Responda só: oi, estou funcionando.", "--tools", ""]
        if model:
            args += ["--model", model]
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip()[:300] or "faça login rodando: claude")
        return r.stdout.strip()
    _, flavor, default_url, default_model = PROVIDERS[provider]
    api = (Anthropic if flavor == "anthropic" else OpenAICompatible)(
        api_key, model or default_model, base_url or default_url, "Responda em português, bem curto.")
    api.tools = None
    _, text, _ = api.call([api.user("Diga só: oi, estou funcionando.")])
    return text.strip()
