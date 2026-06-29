// A small "?" help icon that shows an explanation on hover (native title tooltip,
// consistent with how the rest of the cockpit surfaces hints).
export function Help({ text }: { text: string }) {
  return (
    <span className="help" title={text} aria-label={text} role="img">?</span>
  );
}
