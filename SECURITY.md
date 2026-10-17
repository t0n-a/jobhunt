# Security

jobhunt runs on your own machine and acts in your name (applications, messages, GitHub changes), so security
issues matter even though there is no hosted service.

## Reporting

Please report vulnerabilities privately through GitHub's **Report a vulnerability** button (Security tab →
Advisories). Don't open a public issue. You can expect an acknowledgement within a week.

Especially in scope:

- anything that lets someone other than you trigger an action through the Telegram bot or the local web app;
- a way to make jobhunt submit, send or publish without your explicit yes, or to bypass the autopilot gate;
- secrets or personal data leaking into logs, Telegram messages, prompts or committed files;
- the repo audit missing a secret, or a crafted posting/form/README injecting content into what is sent.

## Running it safely

- Keep `.env`, `config.yaml`, `answers.yaml` and `data/` private; they are gitignored for that reason.
- The web app binds to `127.0.0.1` and refuses cross-site and non-localhost requests. Don't expose it to a network.
- Only your Telegram chat (bound during `jobhunt setup` with a one-time code) can command the bot.
