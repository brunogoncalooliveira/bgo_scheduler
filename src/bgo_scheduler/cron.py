"""Mini-parser de expressões cron (5 campos), sem dependências.

    ┌──────────── minuto        (0-59)
    │ ┌────────── hora          (0-23)
    │ │ ┌──────── dia do mês    (1-31)
    │ │ │ ┌────── mês           (1-12)
    │ │ │ │ ┌──── dia da semana (0-7; 0 e 7 = domingo)
    │ │ │ │ │
    0 9 * * 1-5      -> dias úteis às 09:00

Suporta: "*", listas "1,2,3", intervalos "1-5", passos "*/15" e "1-5/2".
Semântica standard: se dia-do-mês E dia-da-semana forem ambos restritos,
basta um deles casar (OR); caso contrário aplica-se AND.
"""

from datetime import datetime, timedelta


class CronError(ValueError):
    pass


_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _parse_field(field: str, lo: int, hi: int) -> set:
    values = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"campo vazio em '{field}'")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                raise CronError(f"passo inválido '{step_s}'") from None
            if step <= 0:
                raise CronError(f"passo inválido '{step_s}'")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronError(f"intervalo inválido '{part}'") from None
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise CronError(f"valor inválido '{part}'") from None
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise CronError(f"'{part}' fora do intervalo {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return values


class CronSpec:
    def __init__(self, expr: str):
        self.expr = expr.strip()
        parts = self.expr.split()
        if len(parts) != 5:
            raise CronError(
                f"expressão cron deve ter 5 campos (min hora dia mês dia-semana), tem {len(parts)}"
            )
        fields = [_parse_field(p, lo, hi) for p, (lo, hi) in zip(parts, _BOUNDS)]
        self.minutes, self.hours, self.doms, self.months, dows = fields
        # 7 também é domingo
        self.dows = {0 if d == 7 else d for d in dows}
        self.dom_any = parts[2] == "*"
        self.dow_any = parts[4] == "*"

    def _day_ok(self, dt: datetime) -> bool:
        dom_ok = dt.day in self.doms
        cron_dow = (dt.weekday() + 1) % 7  # weekday(): 2ª=0 ... dom=6 -> cron: dom=0
        dow_ok = cron_dow in self.dows
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow_ok
        if self.dow_any:
            return dom_ok
        return dom_ok or dow_ok

    def matches(self, dt: datetime) -> bool:
        return (dt.minute in self.minutes and dt.hour in self.hours
                and dt.month in self.months and self._day_ok(dt))

    def next_after(self, dt: datetime) -> datetime:
        """Próxima ocorrência estritamente depois de dt (hora local, naive)."""
        t = (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(1500):  # ~4 anos de dias (cobre 29 de fevereiro)
            if t.month in self.months and self._day_ok(t):
                for h in sorted(self.hours):
                    if h < t.hour:
                        continue
                    for m in sorted(self.minutes):
                        if h == t.hour and m < t.minute:
                            continue
                        return t.replace(hour=h, minute=m)
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
        raise CronError(f"'{self.expr}' sem ocorrências nos próximos 4 anos")
