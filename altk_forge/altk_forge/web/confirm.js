// Confirmation and message modals shared by every page, in place of the browser's confirm() / alert().
// Built on <dialog>, styled with the page's tokens. Every call returns a promise:
//   await ask.confirm({ title, message, ok: "Delete", danger: true })   -> true | false
//   await ask.choose({ title, message, actions: [{ label, value, kind }] }) -> value | null (cancelled)
//   await ask.alert({ title, message })                                 -> undefined
// message is plain text; blank lines separate paragraphs. details (optional) is a list shown under it.
(function () {
  const css = `
.ask { border: 1px solid var(--line-2); border-radius: 12px; background: var(--surface); color: var(--fg); padding: 0;
  width: min(460px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); box-shadow: 0 18px 50px rgb(0 0 0 / 0.35); }
.ask::backdrop { background: var(--backdrop, rgb(0 0 0 / 0.5)); }
.ask[open] { animation: ask-in 0.14s ease-out; }
@keyframes ask-in { from { opacity: 0; transform: translateY(6px) scale(0.98); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .ask[open] { animation: none; } }
.ask form { display: grid; gap: 16px; padding: 20px 20px 16px; margin: 0; }
.ask-head { display: flex; gap: 12px; align-items: flex-start; }
.ask-icon { flex: none; width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 16px;
  color: var(--data); background: var(--surface-3); }
.ask.danger .ask-icon { color: var(--critical); background: var(--critical-bg); }
.ask.warn .ask-icon { color: var(--warning); background: var(--warning-bg); }
.ask-text { display: grid; gap: 8px; min-width: 0; padding-top: 4px; }
.ask h3 { margin: 0; font-size: 16px; font-weight: 600; overflow-wrap: anywhere; }
.ask p { margin: 0; font-size: 14px; line-height: 1.5; color: var(--fg-2); overflow-wrap: anywhere; }
.ask ul { margin: 0; padding: 8px 10px 8px 26px; border-radius: 6px; background: var(--critical-bg); color: var(--critical);
  font: 12px/1.5 var(--mono, monospace); max-height: 180px; overflow: auto; overflow-wrap: anywhere; }
.ask-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.ask-actions button { font: inherit; font-size: 14px; border: 1px solid var(--line-2); background: var(--surface-2); color: var(--fg);
  border-radius: 6px; padding: 6px 14px; cursor: pointer; }
.ask-actions button:hover { background: var(--surface-3); }
.ask-actions button:focus-visible { outline: 2px solid var(--data); outline-offset: 2px; }
.ask-actions button.primary { background: var(--data); border-color: var(--data); color: var(--on-data, #fff); font-weight: 600; }
.ask-actions button.danger { background: var(--critical); border-color: var(--critical); color: var(--on-data, #fff); font-weight: 600; }
.ask-actions button.primary:hover, .ask-actions button.danger:hover { filter: brightness(1.08); }
.ask-actions .cancel { margin-right: auto; }
@media (max-width: 480px) { .ask-actions { flex-direction: column-reverse; align-items: stretch; } .ask-actions .cancel { margin-right: 0; } }`;

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  let styled = false;

  // actions: [{ label, value, kind: "primary" | "danger" | "", cancel?: true }]. Escape and the cancel action resolve null.
  function open({ title, message = "", details = [], tone = "", actions }) {
    if (!styled) { document.head.append(el("style", null, css)); styled = true; }
    const dlg = el("dialog", `ask ${tone}`);
    const form = el("form");
    form.method = "dialog";
    const head = el("div", "ask-head");
    head.append(el("span", "ask-icon", tone === "danger" || tone === "warn" ? "!" : "i"));
    head.firstChild.setAttribute("aria-hidden", "true");
    const text = el("div", "ask-text");
    const h = el("h3", null, title);
    h.id = `ask-${Math.random().toString(36).slice(2)}`;
    dlg.setAttribute("aria-labelledby", h.id);
    text.append(h);
    for (const para of String(message).split(/\n\s*\n/).filter((s) => s.trim())) text.append(el("p", null, para.trim()));
    if (details.length) { const ul = el("ul"); details.forEach((d) => ul.append(el("li", null, d))); text.append(ul); }
    head.append(text);
    const bar = el("div", "ask-actions");
    let focus = null;
    actions.forEach((a, i) => {
      const b = el("button", [a.kind, a.cancel && "cancel"].filter(Boolean).join(" "), a.label);
      b.value = String(i);
      bar.append(b);
      // Destructive questions start on Cancel so Enter does not delete anything by accident.
      if (tone === "danger" ? a.cancel : a.kind === "primary" || a.kind === "danger") focus ??= b;
    });
    form.append(head, bar);
    dlg.append(form);
    document.body.append(dlg);
    return new Promise((resolve) => {
      dlg.addEventListener("close", () => {
        const a = actions[Number(dlg.returnValue)];
        dlg.remove();
        resolve(a && !a.cancel ? a.value : null);
      });
      dlg.showModal();
      (focus || bar.lastChild).focus();
    });
  }

  const cancel = (label = "Cancel") => ({ label, value: null, cancel: true });
  window.ask = {
    confirm: ({ ok = "OK", danger = false, warn = false, cancelLabel, ...rest }) =>
      open({ ...rest, tone: danger ? "danger" : warn ? "warn" : "", actions: [cancel(cancelLabel), { label: ok, value: true, kind: danger ? "danger" : "primary" }] })
        .then((v) => v === true),
    choose: ({ actions, danger = false, warn = false, cancelLabel, ...rest }) =>
      open({ ...rest, tone: danger ? "danger" : warn ? "warn" : "", actions: [cancel(cancelLabel), ...actions] }),
    alert: ({ ok = "OK", error = false, ...rest }) =>
      open({ ...rest, tone: error ? "danger" : "", actions: [{ label: ok, value: true, kind: "primary" }] }).then(() => undefined),
  };
})();
