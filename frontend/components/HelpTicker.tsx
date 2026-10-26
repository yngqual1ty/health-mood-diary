const PHONE_DISPLAY = "8 (495) 637-70-70";
const PHONE_HREF = "tel:+74956377070";

const TICKER_BEFORE =
  "Если Вы чувствуете необходимость в экстренной помощи - телефон доверия · ФГБУ «НМИЦ ПН им. В.П. Сербского» Минздрава России. Горячая линия помощи. Круглосуточно ";

function TickerCopy({ decorative = false }: { decorative?: boolean }) {
  return (
    <span className="help-ticker-item" aria-hidden={decorative || undefined}>
      {TICKER_BEFORE}
      {decorative ? (
        <span className="help-ticker-phone">{PHONE_DISPLAY}</span>
      ) : (
        <a className="help-ticker-phone" href={PHONE_HREF}>
          {PHONE_DISPLAY}
        </a>
      )}
    </span>
  );
}

export function HelpTicker() {
  return (
    <div className="help-ticker" role="region" aria-label="Горячая линия помощи">
      <div className="help-ticker-track">
        <TickerCopy />
        <TickerCopy decorative />
      </div>
    </div>
  );
}
