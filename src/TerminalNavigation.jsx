import React, { useEffect, useRef, useState } from "react";
import "./navigation.css";

const PRIMARY = ["Overview", "Rankings", "Astra", "Portfolio"];

export default function TerminalNavigation({ tabs, metadata, active, onSelect }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef(null);
  const trigger = useRef(null);
  const secondary = tabs.filter((tab) => !PRIMARY.includes(tab));
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else if (dialog.current?.open) dialog.current.close();
  }, [open]);
  const choose = (tab) => { onSelect(tab); setOpen(false); };
  const button = (tab) => <button key={tab} type="button" className={active === tab ? "active" : ""}
    aria-current={active === tab ? "page" : undefined} onClick={() => choose(tab)}>
    <span aria-hidden="true">{metadata[tab].icon}</span><strong>{metadata[tab].label}</strong></button>;
  return <>
    <nav className="terminal-desktop-nav" aria-label="Terminal sections">{tabs.map(button)}</nav>
    <nav className="terminal-mobile-nav" aria-label="Main sections">{PRIMARY.map(button)}
      <button ref={trigger} type="button" className={secondary.includes(active) ? "active" : ""}
        aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}><span aria-hidden="true">⋯</span><strong>More</strong></button>
    </nav>
    <dialog ref={dialog} className="terminal-more" aria-labelledby="terminal-more-title"
      onCancel={() => setOpen(false)} onClose={() => { setOpen(false); trigger.current?.focus(); }}
      onClick={(event) => { if (event.target === dialog.current) setOpen(false); }}>
      <div className="terminal-more-content"><div className="terminal-more-heading"><h2 id="terminal-more-title">More sections</h2>
        <button type="button" onClick={() => setOpen(false)} aria-label="Close menu">×</button></div>
        <div className="terminal-more-grid">{secondary.map(button)}</div>
      </div>
    </dialog>
  </>;
}
