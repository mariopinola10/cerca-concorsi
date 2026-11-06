// Cerca Concorsi - Cloudflare Worker per il bot Telegram (piano gratuito).
//
// Telegram manda ogni messaggio del bot a questo Worker (webhook). Il Worker:
//   1. controlla che arrivi davvero da Telegram (secret token) e dalla tua chat;
//   2. risponde subito "⏳ Ricevuto";
//   3. avvia il workflow "Bot Telegram" su GitHub passando il messaggio (repository_dispatch).
//
// Variabili del Worker (Settings -> Variables and Secrets):
//   TELEGRAM_TOKEN    (Secret) token del bot da BotFather
//   TELEGRAM_CHAT_ID  (Secret) il tuo chat id
//   GITHUB_TOKEN      (Secret) token GitHub fine-grained, solo questo repository, permesso "Contents: Read and write"
//   WEBHOOK_SECRET    (Secret) una parola segreta inventata da te (lettere, numeri, _ e -)
//   GITHUB_REPO       (Text)   es. mariopinola10/cerca-concorsi

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("Cerca Concorsi bot: attivo");
    }
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("non autorizzato", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }
    const msg = update.message || {};
    const chat = String((msg.chat || {}).id || "");
    if (!chat) return new Response("ok");

    if (chat !== String(env.TELEGRAM_CHAT_ID).trim()) {
      ctx.waitUntil(telegram(env, { chat_id: chat, text: "Questo è un bot privato." }));
      return new Response("ok");
    }

    ctx.waitUntil((async () => {
      const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "cerca-concorsi-bot",
        },
        body: JSON.stringify({ event_type: "telegram", client_payload: { update } }),
      });
      const testo = r.ok
        ? "⏳ Ricevuto, ci penso io: rispondo tra circa un minuto…"
        : `⚠️ Non riesco ad avviare GitHub (errore ${r.status}). Controlla GITHUB_TOKEN e GITHUB_REPO nel Worker.`;
      await telegram(env, { chat_id: chat, text: testo });
    })());

    // Telegram vuole una risposta veloce: il resto continua in background
    return new Response("ok");
  },
};

function telegram(env, corpo) {
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(corpo),
  });
}
