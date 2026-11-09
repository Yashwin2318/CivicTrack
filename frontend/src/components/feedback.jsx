/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

const ToastContext = createContext(null);
const ConfirmContext = createContext(null);
let toastSeq = 0;

export function FeedbackProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [dialog, setDialog] = useState(null);

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);
  const notify = useCallback((message, tone = "success") => {
    const id = ++toastSeq;
    setToasts((list) => [...list.slice(-3), { id, message, tone }]);
    setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4500);
  }, [dismiss]);

  const confirm = useCallback((options) => new Promise((resolve) => setDialog({ ...options, resolve })), []);

  function closeDialog(result) {
    dialog?.resolve(result);
    setDialog(null);
  }

  return (
    <ToastContext.Provider value={notify}>
      <ConfirmContext.Provider value={confirm}>
        {children}
        <div className="ct-toasts" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`ct-toast ct-toast-${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
              {t.tone === "error" ? <AlertTriangle size={18} /> : t.tone === "info" ? <Info size={18} /> : <CheckCircle2 size={18} />}
              <span>{t.message}</span>
              <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss notification"><X size={15} /></button>
            </div>
          ))}
        </div>
        {dialog && <ConfirmDialog {...dialog} onClose={closeDialog} />}
      </ConfirmContext.Provider>
    </ToastContext.Provider>
  );
}

function ConfirmDialog({ title, message, confirmLabel = "Confirm", tone = "primary", requireText, textLabel, textMinLength = 0, onClose }) {
  const [text, setText] = useState("");
  const ref = useRef(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const invalid = requireText && text.trim().length < textMinLength;
  return (
    <div className="ct-modal-backdrop" role="presentation" onKeyDown={(e) => e.key === "Escape" && onClose(false)}>
      <form className="ct-modal ct-modal-sm" role="dialog" aria-modal="true" aria-labelledby="ct-confirm-title"
        onSubmit={(e) => { e.preventDefault(); if (!invalid) onClose(requireText ? text.trim() : true); }}>
        <h3 id="ct-confirm-title">{title}</h3>
        {message && <p className="ct-muted">{message}</p>}
        {requireText && (
          <label className="ct-field">
            {textLabel}
            <textarea ref={ref} rows={3} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} />
            {textMinLength > 0 && <small className="ct-muted">Minimum {textMinLength} characters</small>}
          </label>
        )}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={() => onClose(false)} ref={requireText ? undefined : ref}>Cancel</button>
          <button type="submit" className={`ct-btn ${tone === "danger" ? "ct-btn-danger" : "ct-btn-primary"}`} disabled={invalid}>{confirmLabel}</button>
        </div>
      </form>
    </div>
  );
}

export function useToast() {
  return useContext(ToastContext) || (() => {});
}

export function useConfirm() {
  return useContext(ConfirmContext) || (async () => true);
}
