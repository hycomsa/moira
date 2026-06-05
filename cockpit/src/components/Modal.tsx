import type { ReactNode } from "react";

export function Modal({ eyebrow, title, onClose, footer, children, width }: {
  eyebrow?: string; title: string; onClose: () => void;
  footer?: ReactNode; children: ReactNode; width?: number;
}) {
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" style={width ? { width } : undefined} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            {eyebrow && <div className="modal-eyebrow">{eyebrow}</div>}
            <div className="modal-title">{title}</div>
          </div>
          <button className="modal-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
