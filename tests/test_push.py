"""The two things that stopped the first real push, and nothing about the network.

26 October. The job said it had sent the notice; the phone stayed dark. The log said:

    ! push to one device failed (VapidException)

and that was all it said. It took an afternoon and a reproduction to turn that into two
plain facts, both of which are checked here so that neither can come back quietly.

ONE. The 'sub' claim has to be a mailto:. The spec allows an https URL, py_vapid does
not, and what it says when you give it one is "Missing 'sub' from claims" - about a
claim that is present and perfectly well formed.

TWO. The key has to be a PEM FILE or a base64url DER string. It cannot be the PEM TEXT,
which is exactly what a person copies out of a .pem and pastes into a repository secret,
because a secret cannot be a file. What it says then is "Could not deserialize key data.
The data may be in an incorrect format", which names everything except the one thing
that is wrong.

Neither of these is about the network, so neither is tested against one. The push itself
is sent to a server this test starts on the loopback: if the request arrives, the message
was signed, which is the whole question.
"""
import base64
import json
import os
import sys
import threading
import http.server
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")
import update  # noqa: E402

from cryptography.hazmat.primitives import serialization          # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec          # noqa: E402

fails = []

# ------------------------------------------------------------------ ONE: the subject
if not update.VAPID_SUBJECT.startswith("mailto:"):
    fails.append(f"the VAPID subject is {update.VAPID_SUBJECT!r}. py_vapid takes a"
                 f" mailto: and refuses everything else - including an https URL, which"
                 f" is what the page's own address is and what this used to be")
if "@" not in update.VAPID_SUBJECT:
    fails.append(f"the VAPID subject is not an address: {update.VAPID_SUBJECT!r}")

# ------------------------------------------------------------------- TWO: the key
key = ec.generate_private_key(ec.SECP256R1())
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
converted = update.vapid_key(pem)
if "-----BEGIN" in converted:
    fails.append("vapid_key handed the PEM text straight through; py_vapid cannot read it")
else:
    try:
        raw = base64.urlsafe_b64decode(converted + "=" * (-len(converted) % 4))
        serialization.load_der_private_key(raw, password=None)
    except Exception as exc:
        fails.append(f"what vapid_key produced is not a key py_vapid could load: {exc}")

# A key already in the shape py_vapid wants must be left alone.
if update.vapid_key("Abc-123_xyz") != "Abc-123_xyz":
    fails.append("vapid_key mangled a key that was already base64url")

# ------------------------------------------- and the whole thing, against a local server
# No network: the endpoint is a server this test starts. If a request arrives at all, the
# payload was encrypted and the headers were signed, which is everything the job does.
try:
    from pywebpush import webpush
except ImportError:
    webpush = None

if webpush is None:
    print("(pywebpush is not installed here, so only the two causes are checked)")
else:
    got = []

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append({"path": self.path, "auth": self.headers.get("Authorization", ""),
                        "len": int(self.headers.get("Content-Length") or 0)})
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Catch)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    recv = ec.generate_private_key(ec.SECP256R1())
    p256dh = base64.urlsafe_b64encode(recv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
    sub = {"endpoint": f"http://127.0.0.1:{port}/push",
           "keys": {"p256dh": p256dh,
                    "auth": base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()}}

    saved = (update.PUSH_SUBS, update.VAPID_PRIVATE)
    update.PUSH_SUBS = json.dumps([sub])
    update.VAPID_PRIVATE = pem                      # the shape a person actually pastes
    sent = update.send_push("Sørlandet is under way at Funchal",
                            "She was lying still at Funchal and is now moving.")
    update.PUSH_SUBS, update.VAPID_PRIVATE = saved
    srv.shutdown()

    if sent != 1:
        fails.append("send_push reported nothing sent, with a PEM in the secret and a"
                     " server listening - which is the exact case that failed on the 26th")
    if not got:
        fails.append("no request ever reached the push endpoint")
    elif "vapid" not in got[0]["auth"].lower():
        fails.append(f"the request carried no VAPID authorization: {got[0]['auth'][:60]!r}")

print(f"subject {update.VAPID_SUBJECT}, PEM converted to {len(converted)} characters of"
      f" base64url" + (f", and one signed request arrived" if webpush else ""))
if fails:
    print("FAILURES")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
