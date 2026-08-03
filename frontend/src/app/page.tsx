"use client";

import TradingChart from "@/components/TradingChart";
import { Activity, Bell, Settings, Target, TrendingUp, History, Zap } from "lucide-react";
import { useState, useEffect } from "react";

interface SignalData {
  symbol: string;
  type: string;
  timestamp: string;
  entry: number;
  sl: number;
  tp: number;
  lot_size?: number;
  reasons: string[];
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export default function Home() {
  const [activeSymbol, setActiveSymbol] = useState("XAU/USD");
  const [signals, setSignals] = useState<SignalData[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState({ account_balance: 10000.0, risk_percentage: 1.0 });
  const [stats, setStats] = useState({ win_rate: 0, total_trades: 0 });
  const [toasts, setToasts] = useState<any[]>([]);

  const saveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await fetch(`${API_URL}/api/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings)
      });
      setShowSettings(false);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetch(`${API_URL}/api/settings`)
      .then(res => res.json())
      .then(data => setSettings(data))
      .catch(console.error);

    fetch(`${API_URL}/api/stats`)
      .then(res => res.json())
      .then(data => setStats(data))
      .catch(console.error);

    const ws = new WebSocket(`${WS_URL}/ws`);
    
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "TRADE_CLOSED") {
          const toastId = Date.now();
          setToasts(prev => [...prev, {
            id: toastId,
            symbol: payload.trade.symbol,
            status: payload.status,
            pnl: payload.pnl,
            tradeType: payload.trade.type
          }]);
          setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== toastId));
          }, 5000);
        } else if (payload.signal) {
          // Add new signal to the top of the queue
          setSignals(prev => {
            // Check if we already have this exact signal to avoid duplicates in MVP
            const exists = prev.find(s => s.symbol === payload.signal.symbol && s.timestamp === payload.signal.timestamp);
            if (exists) return prev;
            return [payload.signal, ...prev].slice(0, 10); // Keep last 10
          });
        }
      } catch (e) {
        console.error("WS error in page:", e);
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  const getRelativeTime = (isoString: string) => {
    const diffInSeconds = Math.floor((new Date().getTime() - new Date(isoString).getTime()) / 1000);
    if (diffInSeconds < 60) return `${diffInSeconds}s ago`;
    const diffInMinutes = Math.floor(diffInSeconds / 60);
    if (diffInMinutes < 60) return `${diffInMinutes}m ago`;
    return `${Math.floor(diffInMinutes / 60)}h ago`;
  };

  return (
    <div className="min-h-screen bg-[#030303] text-gray-200 selection:bg-emerald-500/30 font-sans relative overflow-hidden flex items-center justify-center">
      
      {/* Immersive Dynamic Background Mesh */}
      <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-[20%] -left-[10%] w-[60%] h-[60%] rounded-full bg-emerald-600/10 blur-[150px] mix-blend-screen animate-pulse" style={{ animationDuration: '8s' }}></div>
        <div className="absolute -bottom-[20%] -right-[10%] w-[60%] h-[60%] rounded-full bg-indigo-600/10 blur-[150px] mix-blend-screen animate-pulse" style={{ animationDuration: '12s' }}></div>
        <div className="absolute top-[30%] right-[10%] w-[40%] h-[40%] rounded-full bg-blue-500/5 blur-[120px] mix-blend-screen"></div>
        {/* Noise overlay for premium texture */}
        <div className="absolute inset-0 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-[0.03] mix-blend-overlay"></div>
      </div>

      {/* Toasts / Notifications */}
      <div className="fixed top-6 right-6 z-50 flex flex-col space-y-3 pointer-events-none">
        {toasts.map(toast => (
          <div key={toast.id} className={`p-4 rounded-[20px] shadow-2xl border backdrop-blur-md flex items-center space-x-4 transition-all duration-500 transform translate-y-0 opacity-100 ${toast.status === 'WIN' ? 'bg-emerald-950/40 border-emerald-500/30 shadow-[0_10px_40px_-10px_rgba(16,185,129,0.3)]' : 'bg-rose-950/40 border-rose-500/30 shadow-[0_10px_40px_-10px_rgba(244,63,94,0.3)]'}`}>
            <div className={`w-12 h-12 rounded-full flex items-center justify-center ${toast.status === 'WIN' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
              {toast.status === 'WIN' ? <Target size={24} /> : <Zap size={24} />}
            </div>
            <div className="pr-2">
              <p className="text-white font-bold tracking-wide">{toast.symbol} {toast.status === 'WIN' ? 'Hit Take Profit! 🎯' : 'Hit Stop Loss 🛑'}</p>
              <p className="text-sm text-gray-400 font-medium mt-0.5">{toast.tradeType} • PnL: <span className={toast.status === 'WIN' ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>{toast.pnl > 0 ? '+' : ''}{toast.pnl}R</span></p>
            </div>
          </div>
        ))}
      </div>

      {/* Settings Modal */}
      {showSettings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-[#111] border border-white/10 p-8 rounded-3xl w-full max-w-md shadow-2xl relative">
            <h2 className="text-2xl font-black text-white mb-6">System Config</h2>
            <form onSubmit={saveSettings} className="space-y-6">
              <div>
                <label className="block text-gray-400 text-xs font-bold uppercase tracking-wider mb-2">Account Balance ($)</label>
                <input 
                  type="number" 
                  step="0.01"
                  className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-white font-mono focus:outline-none focus:border-emerald-500/50 transition-colors"
                  value={settings.account_balance}
                  onChange={(e) => setSettings({...settings, account_balance: parseFloat(e.target.value) || 0})}
                />
              </div>
              <div>
                <label className="block text-gray-400 text-xs font-bold uppercase tracking-wider mb-2">Risk per Trade (%)</label>
                <input 
                  type="number" 
                  step="0.1"
                  className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-white font-mono focus:outline-none focus:border-emerald-500/50 transition-colors"
                  value={settings.risk_percentage}
                  onChange={(e) => setSettings({...settings, risk_percentage: parseFloat(e.target.value) || 0})}
                />
              </div>
              <div className="flex justify-end space-x-4 pt-4 border-t border-white/5">
                <button type="button" onClick={() => setShowSettings(false)} className="px-5 py-2.5 rounded-xl text-gray-400 hover:text-white font-bold transition-colors">Cancel</button>
                <button type="submit" className="px-5 py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-black font-black transition-colors shadow-[0_0_15px_rgba(16,185,129,0.4)]">Save Changes</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="relative z-10 w-full min-h-screen lg:h-screen max-w-[1920px] mx-auto p-4 md:p-6 lg:p-8 flex flex-col lg:flex-row gap-6 md:gap-8 overflow-y-auto lg:overflow-hidden">
        
        {/* Floating Left Sidebar */}
        <aside className="w-20 bg-white/[0.02] backdrop-blur-3xl border border-white/5 rounded-[32px] shadow-2xl flex-col items-center py-8 hidden lg:flex h-full shrink-0">
          <div className="relative group cursor-pointer mb-12">
            <div className="absolute inset-0 bg-emerald-500 blur-xl opacity-40 group-hover:opacity-80 transition-opacity duration-500 rounded-full"></div>
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center relative z-10 shadow-lg shadow-emerald-500/20">
              <Zap size={24} className="text-black fill-black" />
            </div>
          </div>
          
          <nav className="flex-1 flex flex-col items-center space-y-8 w-full">
            <NavItem icon={<Activity size={22} />} active tooltip="Live Dashboard" />
            <NavItem icon={<TrendingUp size={22} />} tooltip="Alpha Signals" />
            <NavItem icon={<History size={22} />} tooltip="Performance" />
          </nav>
          
          <div className="flex flex-col items-center space-y-8 w-full">
            <NavItem icon={<Bell size={22} />} tooltip="Alerts" />
            <NavItem icon={<Settings size={22} />} onClick={() => setShowSettings(true)} tooltip="System Config" />
          </div>
        </aside>

        {/* Main Center Content */}
        <main className="flex-1 flex flex-col lg:h-full lg:overflow-hidden gap-6">
          
          {/* Header */}
          <header className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-4 pb-2 shrink-0">
            <div>
              <h1 className="text-3xl md:text-4xl font-black text-transparent bg-clip-text bg-gradient-to-r from-white to-white/50 tracking-tight font-display drop-shadow-sm">Novaire EA</h1>
              <p className="text-emerald-400/80 text-xs md:text-sm font-medium tracking-widest uppercase mt-1 md:mt-2">Institutional SMC Engine</p>
            </div>
            
            <div className="flex space-x-2 md:space-x-3 overflow-x-auto w-full sm:w-auto pb-2 sm:pb-0 scrollbar-hide">
              {['XAU/USD'].map((pair) => (
                <button 
                  key={pair} 
                  onClick={() => setActiveSymbol(pair)}
                  className={`flex-shrink-0 px-4 md:px-5 py-2 md:py-2.5 rounded-xl md:rounded-2xl text-xs md:text-sm font-bold tracking-wide transition-all duration-300 ${activeSymbol === pair ? 'bg-white/10 text-white shadow-[0_0_20px_rgba(255,255,255,0.05)] border border-white/20' : 'bg-transparent text-gray-500 hover:text-white hover:bg-white/5 border border-transparent'}`}>
                  {pair}
                </button>
              ))}
            </div>
          </header>

          {/* Chart Area */}
          <div className="w-full flex-grow min-h-[400px] lg:min-h-0 shrink-0 lg:shrink">
            <TradingChart symbol={activeSymbol} />
          </div>
          
          {/* Bottom Stats Floating Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 lg:gap-6 shrink-0 lg:h-36">
            <StatsCard title="Monthly Winrate" value={`${stats.win_rate}%`} trend={`${stats.total_trades} trades`} highlight="emerald" />
            <StatsCard title="Active Protocols" value="3" highlight="blue" />
            <StatsCard title="Engine Status" value="Online" icon={<Activity className="text-emerald-400 animate-pulse" />} highlight="emerald" />
          </div>
        </main>

        {/* Floating Right Sidebar (Signal Queue) */}
        <aside className="w-full lg:w-[400px] bg-white/[0.02] backdrop-blur-3xl border border-white/5 rounded-[32px] flex flex-col min-h-[500px] lg:h-full shadow-2xl relative overflow-hidden shrink-0 lg:shrink">
          {/* subtle glow for sidebar */}
          <div className="absolute top-0 right-0 w-64 h-64 bg-emerald-500/5 rounded-full blur-[80px] pointer-events-none"></div>
          
          <div className="p-8 pb-4">
            <h3 className="font-display font-black text-xl text-white flex items-center space-x-3 tracking-wide">
              <Target size={22} className="text-emerald-400" />
              <span>Alpha Queue</span>
            </h3>
          </div>
          
          <div className="flex-1 overflow-y-auto p-6 pt-2 space-y-6 custom-scrollbar">
            
            {signals.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 px-4 text-center rounded-[24px] bg-white/[0.01] border border-white/5 border-dashed mt-12">
                <div className="relative mb-6">
                  <div className="absolute inset-0 border-2 border-emerald-500 rounded-full animate-ping opacity-20"></div>
                  <div className="absolute inset-[-10px] border border-emerald-500/30 rounded-full animate-ping opacity-10" style={{ animationDelay: '0.2s' }}></div>
                  <div className="w-14 h-14 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center backdrop-blur-md">
                    <Activity size={24} className="text-emerald-400" />
                  </div>
                </div>
                <p className="text-sm text-gray-300 font-bold tracking-wide">Menghubungkan ke Algoritma...</p>
                <p className="text-xs text-gray-500 mt-2 font-medium">Menunggu konfirmasi sinyal SMC</p>
              </div>
            ) : (
              signals.map((sig, idx) => {
                const isBuy = sig.type.includes("BUY");
                const colorClass = isBuy ? "emerald" : "rose";
                const riskReward = Math.abs((sig.tp - sig.entry) / (sig.entry - sig.sl)).toFixed(1);
                
                return (
                  <div key={idx} className={`bg-white/[0.03] border border-white/10 rounded-[24px] p-6 shadow-xl hover:bg-white/[0.05] hover:border-${colorClass}-500/30 hover:-translate-y-1 transition-all duration-500 group relative overflow-hidden cursor-pointer backdrop-blur-sm`}>
                    <div className={`absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-${colorClass}-400 to-transparent opacity-50 group-hover:opacity-100 transition-opacity`}></div>
                    
                    <div className="flex justify-between items-center mb-5">
                      <div className="flex items-center space-x-3">
                        <span className={`text-[10px] font-black px-3 py-1.5 bg-${colorClass}-500/20 text-${colorClass}-400 rounded-lg tracking-widest uppercase border border-${colorClass}-500/20 shadow-[0_0_10px_rgba(16,185,129,0.2)]`}>
                          {sig.type}
                        </span>
                      </div>
                      <span className="text-xs text-gray-500 font-mono font-medium">{getRelativeTime(sig.timestamp)}</span>
                    </div>

                    <h4 className="text-2xl text-white font-black tracking-tight mb-6">{sig.symbol}</h4>
                    
                    <div className="grid grid-cols-2 gap-y-6 gap-x-4 text-sm mb-6">
                      <div>
                        <p className="text-gray-500 text-xs font-semibold tracking-wider uppercase mb-1">Entry</p>
                        <p className="font-mono text-gray-100 text-lg">{sig.entry.toFixed(2)}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 text-xs font-semibold tracking-wider uppercase mb-1">Stop Loss</p>
                        <p className={`font-mono text-rose-400 text-lg`}>{sig.sl.toFixed(2)}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 text-xs font-semibold tracking-wider uppercase mb-1">Take Profit</p>
                        <p className={`font-mono text-emerald-400 text-lg`}>{sig.tp.toFixed(2)}</p>
                      </div>
                      <div>
                        <p className="text-gray-500 text-xs font-semibold tracking-wider uppercase mb-1">Risk / Rwd</p>
                        <p className="font-mono text-gray-100 text-lg">1 : {riskReward}</p>
                      </div>
                      <div className="col-span-2 pt-2 border-t border-white/5">
                        <p className="text-gray-500 text-xs font-semibold tracking-wider uppercase mb-1">Rec. Lot Size ({settings.risk_percentage}% Risk)</p>
                        <p className="font-mono text-blue-400 text-lg">{sig.lot_size ? sig.lot_size.toFixed(2) : "N/A"}</p>
                      </div>
                    </div>
                    
                    <div className="space-y-2 pt-4 border-t border-white/5">
                      {sig.reasons.map((reason, rIdx) => (
                        <p key={rIdx} className="text-xs text-gray-400 flex items-center space-x-2 font-medium">
                          <span className={`w-1.5 h-1.5 rounded-full bg-${colorClass}-500 shadow-[0_0_5px_rgba(16,185,129,0.8)]`}></span> 
                          <span>{reason}</span>
                        </p>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
            
          </div>
        </aside>

      </div>
    </div>
  );
}

// Subcomponents
function NavItem({ icon, active, tooltip, onClick }: { icon: React.ReactNode, active?: boolean, tooltip: string, onClick?: () => void }) {
  return (
    <button onClick={onClick} className={`w-12 h-12 rounded-[16px] flex items-center justify-center transition-all duration-300 group relative
      ${active ? 'bg-white/10 text-white shadow-[0_0_15px_rgba(255,255,255,0.05)] border border-white/10' : 'text-gray-500 hover:text-white hover:bg-white/5'}`}>
      {icon}
      
      {/* Tooltip */}
      <span className="absolute left-16 px-3 py-1.5 bg-black/80 backdrop-blur-md text-white text-xs font-bold tracking-wide rounded-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 border border-white/10 pointer-events-none">
        {tooltip}
      </span>
    </button>
  );
}

function StatsCard({ title, value, trend, icon, highlight }: { title: string, value: string, trend?: string, icon?: React.ReactNode, highlight: 'emerald' | 'blue' }) {
  const glowColor = highlight === 'emerald' ? 'group-hover:bg-emerald-500/10' : 'group-hover:bg-blue-500/10';
  const textColor = highlight === 'emerald' ? 'group-hover:text-emerald-400' : 'group-hover:text-blue-400';

  return (
    <div className={`bg-white/[0.02] backdrop-blur-md border border-white/5 rounded-[24px] p-6 hover:bg-white/[0.04] hover:border-white/10 hover:-translate-y-1 transition-all duration-500 relative overflow-hidden group flex flex-col justify-between h-full shadow-lg`}>
      <div className={`absolute top-0 right-0 w-32 h-32 rounded-full blur-[40px] -mr-10 -mt-10 transition-colors duration-500 ${glowColor}`}></div>
      
      <div className="flex justify-between items-start relative z-10">
        <p className="text-gray-400 text-sm font-semibold tracking-wider uppercase">{title}</p>
        {icon && <div className="p-2 bg-white/5 rounded-xl">{icon}</div>}
      </div>
      
      <div className="flex items-end space-x-3 relative z-10 mt-4">
        <h3 className={`text-4xl font-black text-white font-mono tracking-tighter transition-colors duration-500 ${textColor}`}>{value}</h3>
        {trend && <span className="text-xs text-emerald-400 bg-emerald-500/10 px-2 py-1 rounded-md font-bold border border-emerald-500/20 mb-1">{trend}</span>}
      </div>
    </div>
  );
}
