"""The assistant's body: drives a VTube Studio model through its public API (ws://localhost:8001).

Mouth follows the volume of her voice, the head tilts while she thinks, and the emotion
tag she puts in her replies switches the model's matching expression (if it has one).
"""
import json
import os
import random
import threading
import time
import uuid

import websocket

URL = "ws://localhost:8001"
NAME = os.getenv("ASSISTANT_NAME", "Assistente")
PLUGIN = {"pluginName": NAME[:32], "pluginDeveloper": "Maid Me"}
FPS = 30
EMOTION_HOLD = 4  # seconds an expression stays on after she stops talking
MOUTH_GAIN = 7

# words looked for in the model's expression names, per emotion
EXPRESSION_WORDS = {
    "feliz": ["feliz", "happy", "smile", "joy", "sorri", "laugh"],
    "brava": ["brava", "angry", "mad", "raiva", "annoyed"],
    "triste": ["triste", "sad", "cry", "upset"],
    "surpresa": ["surpresa", "surprise", "shock", "wow"],
    "convencida": ["convencida", "smug", "proud", "smirk"],
    "neutra": ["neutra", "neutral", "calm"],
}
SMILE = {"feliz": 1.0, "convencida": 0.6, "brava": -0.5, "triste": -0.6}


class VTubeBody:
    def __init__(self, voice, token_file):
        self._voice = voice
        self._token_file = token_file
        self._ws = None
        self._lock = threading.Lock()
        self._expressions = []  # [{"file", "name", "active"}] of the loaded model
        self._emotion = None
        self._active_expression = None
        self._quiet_since = None
        self._mouth = 0.0
        self.thinking = False
        threading.Thread(target=self._run, daemon=True).start()

    def set_emotion(self, emotion):
        self._emotion = None if emotion == "neutra" else emotion
        self._quiet_since = None
        if self._ws:
            try:
                self._apply_expression()
            except Exception:
                pass

    # --- connection --------------------------------------------------------
    def _run(self):
        warned = False
        while True:
            try:
                self._connect()
                print("[corpo] conectada ao VTube Studio")
                warned = False
                self._loop()
            except Exception as e:
                if not warned:
                    print(f"[corpo] VTube Studio indisponivel ({e.__class__.__name__}); tentando de novo em segundo plano")
                    warned = True
            self._ws = None
            time.sleep(5)

    def _connect(self):
        self._ws = websocket.create_connection(URL, timeout=5)
        token = self._read_token()
        if token and self._authenticate(token):
            return
        print("[corpo] aceite o pedido de permissao do plugin dentro do VTube Studio")
        self._ws.settimeout(120)  # waits for the user to click Allow
        token = self._request("AuthenticationTokenRequest", PLUGIN)["authenticationToken"]
        self._ws.settimeout(5)
        with open(self._token_file, "w", encoding="utf-8") as f:
            f.write(token)
        if not self._authenticate(token):
            raise RuntimeError("autenticacao recusada")

    def _authenticate(self, token):
        data = self._request("AuthenticationRequest", {**PLUGIN, "authenticationToken": token})
        return data.get("authenticated", False)

    def _read_token(self):
        try:
            with open(self._token_file, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return None

    def _request(self, message_type, data=None):
        req = {"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0", "requestID": uuid.uuid4().hex,
               "messageType": message_type, "data": data or {}}
        with self._lock:
            self._ws.send(json.dumps(req))
            while True:
                resp = json.loads(self._ws.recv())
                if resp.get("requestID") == req["requestID"]:
                    break
        if resp.get("messageType") == "APIError":
            raise RuntimeError(resp["data"].get("message"))
        return resp.get("data", {})

    # --- animation -----------------------------------------------------------
    def _loop(self):
        last_scan = 0
        while True:
            now = time.time()
            if now - last_scan > 10:  # the user may switch models
                self._expressions = self._request("ExpressionStateRequest", {"details": False}).get("expressions", [])
                last_scan = now
                if not self._active_expression:
                    self._apply_expression()
            self._release_emotion_when_quiet(now)
            self._request("InjectParameterDataRequest", {
                "faceFound": False, "mode": "set", "parameterValues": self._parameters()})
            time.sleep(1 / FPS)

    def _parameters(self):
        target = min(1.0, self._voice.level * MOUTH_GAIN)
        # open fast, close a bit slower so it doesn't flicker
        self._mouth = target if target > self._mouth else self._mouth * 0.6 + target * 0.4
        params = [{"id": "MouthOpen", "value": round(self._mouth, 3)}]
        if self._emotion in SMILE:
            params.append({"id": "MouthSmile", "value": SMILE[self._emotion]})
        if self.thinking:
            params += [{"id": "FaceAngleZ", "value": 9}, {"id": "FaceAngleY", "value": 8},
                       {"id": "EyeRightY", "value": 0.6}, {"id": "EyeLeftY", "value": 0.6}]
        return params

    def _release_emotion_when_quiet(self, now):
        if not self._emotion or not self._voice.idle.is_set():
            self._quiet_since = None
            return
        self._quiet_since = self._quiet_since or now
        if now - self._quiet_since > EMOTION_HOLD:
            self._emotion = None
            self._apply_expression()

    def _apply_expression(self):
        # with no emotion the model rests on its calm face instead of looking sad
        wanted = self._find_expression(self._emotion or "neutra")
        if wanted == self._active_expression:
            return
        if self._active_expression:
            self._request("ExpressionActivationRequest", {"expressionFile": self._active_expression, "active": False})
        if wanted:
            self._request("ExpressionActivationRequest", {"expressionFile": wanted, "active": True})
        self._active_expression = wanted

    def _find_expression(self, emotion):
        # several variations of the same emotion: pick one at random so she doesn't repeat herself
        words = EXPRESSION_WORDS.get(emotion, [])
        matches = [exp["file"] for exp in self._expressions
                   if any(w in (exp.get("name", "") + " " + exp.get("file", "")).lower() for w in words)]
        return random.choice(matches) if matches else None


class NoBody:
    thinking = False

    def set_emotion(self, emotion):
        pass
