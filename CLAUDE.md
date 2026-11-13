# Arbeidsreglar for Claude i dette repoet

## Testar

Køyr heile pakken før kvar commit:

    bash tests/run.sh

Han treng Python 3.10+, playwright med Chromium, cryptography og Pillow. På Christer sin Mac
ligg dei i `~/.venvs/sorlandet`.

## Push

Når du har committa og alle testane er grøne, push sjølv:

    git pull --rebase && git push

Er éin eller fleire testar raude, skal du ikkje pushe. Sei frå kva som feila.
