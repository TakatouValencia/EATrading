from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy
from smc_engine import SMCEngine
from signal_generator import SignalGenerator
import asyncio

class SMCStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("SMC (Baseline)")
        self.sg = SignalGenerator(cooldown_minutes=0)
        self.sg.client = None # Bypass LLM
        
    async def evaluate(self, symbol: str, current_price: float, current_time: str, 
                 df_ltf: List[Dict], df_htf: List[Dict], df_h1: List[Dict], df_h4: List[Dict]) -> Optional[Dict]:
                 
        # Analisa HTF M15 (actually 1H in original code depending on how data is passed)
        # We pass df_htf as the higher timeframe for trend (which was df_htf = 1H)
        engine_htf = SMCEngine(df_htf)
        htf_events = engine_htf.detect_bos_choch()
        htf_trend = None
        if htf_events:
            if "BULLISH" in htf_events[-1]['type']:
                htf_trend = "BULLISH"
            elif "BEARISH" in htf_events[-1]['type']:
                htf_trend = "BEARISH"
                
        # Analisa H1
        h1_trend = None
        if len(df_h1) >= 50:
            h1_events = SMCEngine(df_h1).detect_bos_choch()
            if h1_events:
                h1_trend = "BULLISH" if "BULLISH" in h1_events[-1]['type'] else "BEARISH"
                
        # Analisa H4
        h4_trend = None
        if len(df_h4) >= 50:
            h4_events = SMCEngine(df_h4).detect_bos_choch()
            if h4_events:
                h4_trend = "BULLISH" if "BULLISH" in h4_events[-1]['type'] else "BEARISH"
                
        # Analisa LTF M15
        engine_ltf = SMCEngine(df_ltf)
        events = engine_ltf.detect_bos_choch()
        fvgs = engine_ltf.detect_fvg()
        obs = engine_ltf.detect_order_blocks(events)
        sweeps = engine_ltf.detect_liquidity_sweeps()
        snr_zones = engine_ltf.detect_support_resistance()
        snd_zones = engine_ltf.detect_supply_demand()
        pd_zones = engine_ltf.detect_premium_discount()
        breakers = engine_ltf.detect_breaker_blocks(events)
        fibo_ote = engine_ltf.detect_fibo_ote()
        poc_price = engine_ltf.calculate_volume_profile(lookback=100)
        amd_setups = engine_ltf.detect_amd()
        atr = engine_ltf.calculate_atr(period=14)
        reversal_patterns = engine_ltf.detect_reversal_patterns()
        
        try:
            signal = await self.sg.evaluate_confluence(
                symbol=symbol,
                current_price=current_price,
                events=events,
                obs=obs,
                fvgs=fvgs,
                sweeps=sweeps,
                htf_trend=htf_trend,
                h1_trend=h1_trend,
                h4_trend=h4_trend,
                snr_zones=snr_zones,
                snd_zones=snd_zones,
                pd_zones=pd_zones,
                breakers=breakers,
                fibo_ote=fibo_ote,
                poc_price=poc_price,
                amd_setups=amd_setups,
                atr=atr,
                reversal_patterns=reversal_patterns,
                engine_ltf=engine_ltf
            )
            
            # Check blacklist
            if signal:
                if self.is_blacklisted(signal['entry']):
                    return None
                    
            return signal
            
        except Exception as e: 
            print(f"Error in SMC evaluate: {e}")
            
        return None


