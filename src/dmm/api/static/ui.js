// ui.js -- shared helpers for the DMM pages: a confirmation dialog for the
// operator actions, and copy-to-clipboard.

(function () {
    let dialog = null;

    function buildDialog() {
        dialog = document.createElement('dialog');
        dialog.className = 'confirm-dialog';
        dialog.innerHTML = `
            <form method="dialog" class="confirm-form">
                <h3 class="confirm-title"></h3>
                <p class="confirm-message"></p>
                <div class="confirm-section confirm-steps-section" hidden>
                    <div class="confirm-heading">What happens</div>
                    <ol class="confirm-steps"></ol>
                </div>
                <div class="confirm-section confirm-when-section" hidden>
                    <div class="confirm-heading">Use it when</div>
                    <p class="confirm-when"></p>
                </div>
                <p class="confirm-note" hidden></p>
                <label class="confirm-field" hidden>
                    <span class="confirm-label"></span>
                    <input class="confirm-input" autocomplete="off">
                </label>
                <p class="confirm-error" role="alert"></p>
                <div class="confirm-buttons">
                    <button type="button" class="btn confirm-cancel">Cancel</button>
                    <button type="submit" class="btn confirm-ok"></button>
                </div>
            </form>`;
        dialog.querySelector('.confirm-cancel').addEventListener('click', () => dialog.close('cancel'));
        // The form fills the dialog, so a click landing on the dialog itself is on the backdrop.
        dialog.addEventListener('click', event => {
            if (event.target === dialog) dialog.close('cancel');
        });
        document.body.appendChild(dialog);
        return dialog;
    }

    /**
     * Ask the operator to confirm an action in a modal dialog.
     *
     * opts: { title, message, confirmLabel, danger,
     *         steps: [what the action does, in order], when: when to use it,
     *         note: a caveat, shown highlighted,
     *         input: { label, type, value, attrs, validate(value) -> error string or null } }
     *
     * Resolves to null when cancelled (Cancel, Escape or a click outside), otherwise
     * to the input's value, or '' when there is no input. Focus starts on Cancel, so
     * a stray Enter or Space does not confirm.
     */
    window.confirmAction = function (opts) {
        const d = dialog || buildDialog();
        const form = d.querySelector('form');
        const field = d.querySelector('.confirm-field');
        const input = d.querySelector('.confirm-input');
        const error = d.querySelector('.confirm-error');
        const ok = d.querySelector('.confirm-ok');

        d.querySelector('.confirm-title').textContent = opts.title;
        d.querySelector('.confirm-message').textContent = opts.message || '';

        const steps = d.querySelector('.confirm-steps');
        steps.replaceChildren(...(opts.steps || []).map(text => {
            const item = document.createElement('li');
            item.textContent = text;
            return item;
        }));
        d.querySelector('.confirm-steps-section').hidden = !(opts.steps && opts.steps.length);
        d.querySelector('.confirm-when').textContent = opts.when || '';
        d.querySelector('.confirm-when-section').hidden = !opts.when;
        const note = d.querySelector('.confirm-note');
        note.textContent = opts.note || '';
        note.hidden = !opts.note;
        ok.textContent = opts.confirmLabel || 'Confirm';
        ok.classList.toggle('btn-danger-solid', !!opts.danger);
        ok.classList.toggle('btn-primary', !opts.danger);
        error.textContent = '';

        field.hidden = !opts.input;
        if (opts.input) {
            d.querySelector('.confirm-label').textContent = opts.input.label || '';
            for (const name of ['min', 'max', 'step', 'placeholder']) input.removeAttribute(name);
            input.type = opts.input.type || 'text';
            for (const [name, value] of Object.entries(opts.input.attrs || {})) input.setAttribute(name, value);
            input.value = opts.input.value ?? '';
        }
        d.returnValue = '';

        return new Promise(resolve => {
            form.onsubmit = event => {
                event.preventDefault();
                const problem = opts.input && opts.input.validate ? opts.input.validate(input.value) : null;
                if (problem) {
                    error.textContent = problem;
                    input.focus();
                    return;
                }
                d.close('ok');
            };
            d.onclose = () => resolve(d.returnValue === 'ok' ? (opts.input ? input.value : '') : null);
            d.showModal();
            if (opts.input) {
                input.focus();
                input.select();
            } else {
                d.querySelector('.confirm-cancel').focus();
            }
        });
    };

    /**
     * Copy text to the clipboard. navigator.clipboard needs a secure context, and
     * DMM is often served over plain HTTP, so fall back to a hidden textarea.
     */
    window.copyText = async function (text) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand('copy');
        area.remove();
        if (!copied) throw new Error('copy failed');
    };

    /** Briefly swap a button's label, e.g. "Copied". */
    window.flashLabel = function (button, text) {
        const original = button.dataset.label || button.textContent;
        button.dataset.label = original;
        button.textContent = text;
        clearTimeout(button._flashTimer);
        button._flashTimer = setTimeout(() => { button.textContent = original; }, 1500);
    };
})();
