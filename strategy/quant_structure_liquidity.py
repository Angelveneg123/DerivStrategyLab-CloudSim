"""Quant Structure Liquidity v0.1.

Primera estrategia propia del laboratorio. Diseñada para backtesting y DEMO.
No promete rentabilidad: implementa hipótesis objetivas para poder medirlas.

Flujo:
    régimen/HTF -> liquidez -> sweep -> MSS/BOS -> POI (FVG/OB)
    -> premium/discount -> filtro de volatilidad -> señal.

Todas las decisiones usan únicamente velas ya cerradas. Los pivotes se vuelven
visibles solo después de sus velas de confirmación para evitar look-ahead bias.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from indicators.atr import calculate_atr
from indicators.ema import calculate_ema
from strategy.market_structure import analizar_estructura, find_swing_points
from strategy.strategy_engine import BUY, SELL, HOLD


@dataclass(frozen=True)
class Zone:
    kind: str          # FVG | OB
    direction: str     # bullish | bearish
    low: float
    high: float
    created_epoch: int
    source_index: int


def _infer_opens(closes):
    if not closes:
        return []
    return [closes[0]] + list(closes[:-1])


def _aggregate_full(epochs, opens, highs, lows, closes, target, source):
    """Agrega OHLC y publica cada vela solo en su cierre real."""
    if target < source or target % source != 0:
        raise ValueError(f"No se puede agregar {source}s a {target}s")
    buckets = []
    current = None
    expected = target // source
    for e, o, h, l, c in zip(epochs, opens, highs, lows, closes):
        start = int(e) - (int(e) % target)
        if current is None or current["start"] != start:
            if current is not None:
                buckets.append(current)
            current = {"start": start, "open": float(o), "high": float(h), "low": float(l),
                       "close": float(c), "first": int(e), "last": int(e), "count": 1}
        else:
            current["high"] = max(current["high"], float(h))
            current["low"] = min(current["low"], float(l))
            current["close"] = float(c)
            current["last"] = int(e)
            current["count"] += 1
    if current is not None:
        buckets.append(current)

    E,O,H,L,C = [],[],[],[],[]
    for b in buckets:
        close_epoch = b["start"] + target
        if b["first"] != b["start"] or b["last"] + source < close_epoch or b["count"] < expected:
            continue
        E.append(close_epoch); O.append(b["open"]); H.append(b["high"]); L.append(b["low"]); C.append(b["close"])
    return E,O,H,L,C


def _strict_structure_bias(highs, lows, lookback=2):
    """Sesgo por los dos últimos swings confirmados de cada tipo."""
    sh, sl = find_swing_points(highs, lows, lookback=lookback)
    out = [None] * len(highs)
    known_h, known_l = [], []
    ih = il = 0
    for i in range(len(highs)):
        while ih < len(sh) and sh[ih][0] <= i:
            known_h.append(sh[ih][1]); ih += 1
        while il < len(sl) and sl[il][0] <= i:
            known_l.append(sl[il][1]); il += 1
        if len(known_h) >= 2 and len(known_l) >= 2:
            if known_h[-1] > known_h[-2] and known_l[-1] > known_l[-2]:
                out[i] = "bullish"
            elif known_h[-1] < known_h[-2] and known_l[-1] < known_l[-2]:
                out[i] = "bearish"
            else:
                out[i] = "range"
    return out


def _ema_alignment(closes):
    """Confirmación opcional; si no hay 200 velas devuelve None."""
    n = len(closes)
    if n < 200:
        return [None] * n
    e50 = calculate_ema(closes, 50); e100 = calculate_ema(closes, 100); e200 = calculate_ema(closes, 200)
    result = [None] * n
    for i in range(n):
        if e200[i] is None or e100[i] is None or e50[i] is None:
            continue
        if closes[i] > e50[i] > e100[i] > e200[i]: result[i] = "bullish"
        elif closes[i] < e50[i] < e100[i] < e200[i]: result[i] = "bearish"
        else: result[i] = "friction"
    return result


def _map_state_by_epoch(source_epochs, state_epochs, states):
    """Estado más reciente cuya vela ya cerró en cada epoch fuente."""
    result = [None] * len(source_epochs)
    j = 0; current = None
    for i, e in enumerate(source_epochs):
        while j < len(state_epochs) and state_epochs[j] <= e:
            if states[j] is not None: current = states[j]
            j += 1
        result[i] = current
    return result


def _sma(values, period):
    out = [None]*len(values)
    for i in range(period-1, len(values)):
        window = [x for x in values[i-period+1:i+1] if x is not None]
        if len(window) == period: out[i] = sum(window)/period
    return out


def _previous_day_levels(epochs, highs, lows):
    """PDH/PDL en UTC; válido también para activos 24/7 como sintéticos."""
    out = [(None,None)]*len(epochs)
    current_day = None; day_h = day_l = None; prev_h = prev_l = None
    for i,(e,h,l) in enumerate(zip(epochs,highs,lows)):
        day = int(e)//86400
        if current_day is None:
            current_day = day; day_h=float(h); day_l=float(l)
        elif day != current_day:
            prev_h, prev_l = day_h, day_l
            current_day = day; day_h=float(h); day_l=float(l)
        else:
            day_h=max(day_h,float(h)); day_l=min(day_l,float(l))
        out[i]=(prev_h,prev_l)
    return out


def _known_swings_by_bar(highs, lows, lookback=2):
    sh, sl = find_swing_points(highs,lows,lookback)
    out=[]; kh=[]; kl=[]; a=b=0
    for i in range(len(highs)):
        while a<len(sh) and sh[a][0]<=i: kh.append(sh[a][1]); a+=1
        while b<len(sl) and sl[b][0]<=i: kl.append(sl[b][1]); b+=1
        out.append((list(kh),list(kl)))
    return out


def _detect_sweeps(opens, highs, lows, closes, atr, swing_memory, pd_levels,
                   wick_ratio_min=0.40, penetration_atr=0.10):
    events=[None]*len(closes)
    for i in range(len(closes)):
        if atr[i] is None or atr[i] <= 0: continue
        rng=highs[i]-lows[i]
        if rng<=0: continue
        known_h,known_l=swing_memory[i]
        pdh,pdl=pd_levels[i]
        high_targets=(known_h[-3:] if known_h else []) + ([pdh] if pdh is not None else [])
        low_targets=(known_l[-3:] if known_l else []) + ([pdl] if pdl is not None else [])
        upper=highs[i]-max(opens[i],closes[i])
        lower=min(opens[i],closes[i])-lows[i]
        for x in reversed(high_targets):
            if highs[i]>x and closes[i]<x and (highs[i]-x)>=penetration_atr*atr[i] and upper/rng>=wick_ratio_min:
                events[i]={"type":"BSL_SWEEP","bias":"bearish","level":x}; break
        if events[i] is not None: continue
        for x in reversed(low_targets):
            if lows[i]<x and closes[i]>x and (x-lows[i])>=penetration_atr*atr[i] and lower/rng>=wick_ratio_min:
                events[i]={"type":"SSL_SWEEP","bias":"bullish","level":x}; break
    return events


def _fvg_at(opens, highs, lows, closes):
    result=[None]*len(closes)
    for i in range(2,len(closes)):
        if lows[i] > highs[i-2]:
            result[i]=Zone("FVG","bullish",float(highs[i-2]),float(lows[i]),0,i)
        elif highs[i] < lows[i-2]:
            result[i]=Zone("FVG","bearish",float(highs[i]),float(lows[i-2]),0,i)
    return result


def _build_h1_zones(epochs, opens, highs, lows, closes):
    """FVGs + OBs vinculados a ruptura estructural en H1."""
    fvg=_fvg_at(opens,highs,lows,closes)
    structure=analizar_estructura(highs,lows,closes,lookback=2)
    breaks={}
    for r in structure["rupturas"]:
        breaks.setdefault(r["index"],[]).append(r)
    zones=[]
    for i,z in enumerate(fvg):
        if z is None: continue
        z=Zone(z.kind,z.direction,z.low,z.high,int(epochs[i]),i); zones.append(z)
        wanted="alcista" if z.direction=="bullish" else "bajista"
        if not any(r["direccion"]==wanted for r in breaks.get(i,[])):
            continue
        # Última vela contraria dentro de las 5 anteriores.
        for j in range(i-1,max(-1,i-6),-1):
            bearish=closes[j] < opens[j]
            bullish=closes[j] > opens[j]
            if (z.direction=="bullish" and bearish) or (z.direction=="bearish" and bullish):
                zones.append(Zone("OB",z.direction,float(lows[j]),float(highs[j]),int(epochs[i]),j))
                break
    return zones


def _latest_dealing_range(highs,lows,at_index,lookback=2):
    sh,sl=find_swing_points(highs,lows,lookback)
    hs=[p for idx,p in sh if idx<=at_index]; ls=[p for idx,p in sl if idx<=at_index]
    if not hs or not ls: return None
    lo=float(ls[-1]); hi=float(hs[-1])
    if hi<=lo: return None
    return lo,hi,(lo+hi)/2


def generate_quant_structure_liquidity_signals(
    epochs, highs, lows, closes, *, opens=None, source_granularity=60,
    htf_granularity=14400, mtf_granularity=3600, ltf_granularity=900,
    swing_lookback=2, wick_ratio_min=0.40, penetration_atr=0.10,
    low_vol_ratio=0.75, event_window=3, require_ema_alignment=False,
):
    """Genera BUY/SELL/HOLD con QSL v0.1.

    HTF=H4, MTF=H1 y LTF=M15 por defecto. Si el historial no contiene 200
    velas HTF, la EMA no se exige; la estructura confirmada sigue siendo el
    sesgo primario. Esto permite probar Crash 500 sin 48k velas M1 de warmup.
    """
    if not (len(epochs)==len(highs)==len(lows)==len(closes)):
        raise ValueError("epochs/highs/lows/closes deben tener el mismo largo")
    if len(closes)<220:
        return [HOLD]*len(closes)
    opens=list(opens) if opens is not None else _infer_opens(closes)
    if len(opens)!=len(closes): raise ValueError("opens debe tener el mismo largo")
    if source_granularity>ltf_granularity:
        raise ValueError("QSL v0.1 requiere datos fuente M15 o inferiores")

    hE,hO,hH,hL,hC=_aggregate_full(epochs,opens,highs,lows,closes,htf_granularity,source_granularity)
    mE,mO,mH,mL,mC=_aggregate_full(epochs,opens,highs,lows,closes,mtf_granularity,source_granularity)
    lE,lO,lH,lL,lC=_aggregate_full(epochs,opens,highs,lows,closes,ltf_granularity,source_granularity)
    if len(hC)<8 or len(mC)<12 or len(lC)<60:
        return [HOLD]*len(closes)

    h_bias=_strict_structure_bias(hH,hL,lookback=swing_lookback)
    h_ema=_ema_alignment(hC)

    latr=calculate_atr(lH,lL,lC,14)
    latr_avg=_sma(latr,50)
    swing_mem=_known_swings_by_bar(lH,lL,swing_lookback)
    pd=_previous_day_levels(lE,lH,lL)
    sweeps=_detect_sweeps(lO,lH,lL,lC,latr,swing_mem,pd,wick_ratio_min,penetration_atr)
    lstruct=analizar_estructura(lH,lL,lC,lookback=swing_lookback)
    structural=[None]*len(lC)
    for r in lstruct["rupturas"]:
        structural[r["index"]] = {"type":r["tipo"],"bias":"bullish" if r["direccion"]=="alcista" else "bearish"}

    zones=_build_h1_zones(mE,mO,mH,mL,mC)
    signals_l=[HOLD]*len(lC)
    h_j=0; h_state=None; h_ema_state=None; m_j=0
    recent_sweep={"bullish":None,"bearish":None}; recent_structure={"bullish":None,"bearish":None}
    for i,e in enumerate(lE):
        while h_j<len(hE) and hE[h_j]<=e:
            if h_bias[h_j] is not None: h_state=h_bias[h_j]
            if h_ema[h_j] is not None: h_ema_state=h_ema[h_j]
            h_j+=1
        while m_j+1<len(mE) and mE[m_j+1]<=e: m_j+=1
        if h_state not in ("bullish","bearish"): continue
        if require_ema_alignment and h_ema_state not in (h_state,None): continue
        if latr[i] is None or latr_avg[i] is None or latr[i] < low_vol_ratio*latr_avg[i]: continue

        ev=sweeps[i]
        if ev: recent_sweep[ev["bias"]]=i
        st=structural[i]
        if st: recent_structure[st["bias"]]=i
        bias=h_state
        si=recent_sweep[bias]; mi=recent_structure[bias]
        # El sweep sí es requisito duro. El resto se puntúa para poder medir
        # qué confluencias aportan edge sin matar la muestra estadística.
        if si is None or i-si>event_window:
            continue

        score = 20  # sesgo HTF estructural
        score += 20  # sweep reciente
        if mi is not None and mi >= si and i-mi <= event_window:
            score += 20

        # POI MTF: toque actual o en las últimas `event_window` velas LTF.
        active=[z for z in zones if z.direction==bias and z.created_epoch<=e]
        touched=False
        for z in reversed(active[-20:]):
            start=max(0,i-event_window)
            if any(lH[k] >= z.low and lL[k] <= z.high for k in range(start,i+1)):
                touched=True; break
        if touched:
            score += 15

        dr=_latest_dealing_range(mH,mL,m_j,swing_lookback)
        fib_ok=False
        if dr is not None:
            _lo,_hi,mid=dr
            fib_ok=(bias=="bullish" and lC[i] <= mid) or (bias=="bearish" and lC[i] >= mid)
        if fib_ok:
            score += 10

        if h_ema_state == bias:
            score += 5
        score += 10  # volatilidad ya superó el gate ATR

        if score >= 70:
            signals_l[i]=BUY if bias=="bullish" else SELL
            recent_sweep[bias]=None
            if mi is not None and mi >= si:
                recent_structure[bias]=None

    # Publicar la señal en la última vela fuente que cierra el bucket M15.
    out=[HOLD]*len(closes)
    end_to_idx={int(e)+int(source_granularity):i for i,e in enumerate(epochs)}
    for e,s in zip(lE,signals_l):
        idx=end_to_idx.get(int(e))
        if idx is not None and s in (BUY,SELL): out[idx]=s
    return out
