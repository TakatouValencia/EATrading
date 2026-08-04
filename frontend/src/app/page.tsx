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
  status: string; // PENDING, ACTIVE, WIN, LOSS, CANCELLED
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
  const [filter, setFilter] = useState("All");

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

    fetch(`${API_URL}/api/signals`)
      .then(res => res.json())
      .then(data => setSignals(data.signals || []))
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
            return [payload.signal, ...prev].slice(0, 50); // Keep last 50
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
    if (!isoString) return "";
    const diffInSeconds = Math.floor((new Date().getTime() - new Date(isoString).getTime()) / 1000);
    if (diffInSeconds < 60) return `${diffInSeconds}s ago`;
    const diffInMinutes = Math.floor(diffInSeconds / 60);
    if (diffInMinutes < 60) return `${diffInMinutes}m ago`;
    return `${Math.floor(diffInMinutes / 60)}h ago`;
  };

  const filteredSignals = signals.filter(sig => {
    if (filter === "All") return true;
    if (filter === "Open") return sig.status === "PENDING" || sig.status === "ACTIVE";
    if (filter === "Hit TP") return sig.status === "WIN";
    if (filter === "Hit SL") return sig.status === "LOSS";
    if (filter === "Closed") return sig.status === "WIN" || sig.status === "LOSS" || sig.status === "CANCELLED";
    return true;
  });

  return (
    <div className="min-h-screen bg-[#0a0a0c] text-gray-200 selection:bg-purple-500/30 font-sans relative overflow-hidden flex items-center justify-center">
      
      {/* Immersive Dynamic Background Mesh */}
      <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-[20%] -left-[10%] w-[60%] h-[60%] rounded-full bg-purple-600/5 blur-[150px] mix-blend-screen animate-pulse" style={{ animationDuration: '8s' }}></div>
        <div className="absolute -bottom-[20%] -right-[10%] w-[60%] h-[60%] rounded-full bg-indigo-600/5 blur-[150px] mix-blend-screen animate-pulse" style={{ animationDuration: '12s' }}></div>
        {/* Noise overlay for premium texture */}
        <div className="absolute inset-0 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-[0.04] mix-blend-overlay"></div>
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
                  className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-white font-mono focus:outline-none focus:border-purple-500/50 transition-colors"
                  value={settings.account_balance}
                  onChange={(e) => setSettings({...settings, account_balance: parseFloat(e.target.value) || 0})}
                />
              </div>
              <div>
                <label className="block text-gray-400 text-xs font-bold uppercase tracking-wider mb-2">Risk per Trade (%)</label>
                <input 
                  type="number" 
                  step="0.1"
                  className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-white font-mono focus:outline-none focus:border-purple-500/50 transition-colors"
                  value={settings.risk_percentage}
                  onChange={(e) => setSettings({...settings, risk_percentage: parseFloat(e.target.value) || 0})}
                />
              </div>
              <div className="flex justify-end space-x-4 pt-4 border-t border-white/5">
                <button type="button" onClick={() => setShowSettings(false)} className="px-5 py-2.5 rounded-xl text-gray-400 hover:text-white font-bold transition-colors">Cancel</button>
                <button type="submit" className="px-5 py-2.5 rounded-xl bg-purple-500 hover:bg-purple-400 text-white font-black transition-colors shadow-[0_0_15px_rgba(168,85,247,0.4)]">Save Changes</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="relative z-10 w-full min-h-screen lg:h-screen max-w-[1920px] mx-auto p-4 md:p-6 lg:p-8 flex flex-col lg:flex-row gap-6 md:gap-8 overflow-y-auto lg:overflow-hidden">
        
        {/* Floating Left Sidebar */}
        <aside className="w-20 bg-[#16161a]/80 backdrop-blur-3xl border border-white/5 rounded-[32px] shadow-2xl flex-col items-center py-8 hidden lg:flex h-full shrink-0">
          <div className="relative group cursor-pointer mb-12">
            <div className="absolute inset-0 bg-purple-500 blur-xl opacity-40 group-hover:opacity-80 transition-opacity duration-500 rounded-full"></div>
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-400 to-purple-600 flex items-center justify-center relative z-10 shadow-lg shadow-purple-500/20">
              <Zap size={24} className="text-white fill-white" />
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
              <p className="text-purple-400/80 text-xs md:text-sm font-medium tracking-widest uppercase mt-1 md:mt-2">Institutional SMC Engine</p>
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
        <aside className="w-full lg:w-[450px] bg-[#121215]/80 backdrop-blur-3xl border border-white/5 rounded-[32px] flex flex-col min-h-[500px] lg:h-full shadow-2xl relative overflow-hidden shrink-0 lg:shrink">
          {/* Header */}
          <div className="p-6 pb-2 flex justify-between items-center">
            <div>
              <h3 className="font-display font-bold text-2xl text-white tracking-wide">
                Signal
              </h3>
              <p className="text-gray-400 text-xs mt-1">Signal XAUUSD terbaru dari Novaire EA</p>
            </div>
            <div className="flex items-center space-x-1 bg-[#1a1a1e] px-3 py-1.5 rounded-full border border-white/5">
              <Bell size={14} className="text-yellow-500 fill-yellow-500" />
              <span className="text-white font-bold text-sm ml-1">{signals.length}</span>
            </div>
          </div>
          
          {/* Filter Pills */}
          <div className="px-6 pb-4 pt-2 flex items-center space-x-2 overflow-x-auto scrollbar-hide shrink-0">
            {['All', 'Open', 'Hit TP', 'Hit SL', 'Closed'].map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-5 py-1.5 rounded-full text-xs font-bold tracking-wide transition-all whitespace-nowrap ${filter === f ? 'bg-purple-600 text-white border border-purple-500' : 'bg-transparent border border-white/10 text-gray-400 hover:text-white hover:bg-white/5'}`}
              >
                {f}
              </button>
            ))}
          </div>
          
          <div className="flex-1 overflow-y-auto p-6 pt-2 space-y-4 custom-scrollbar">
            
            {filteredSignals.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 px-4 text-center rounded-[24px] bg-white/[0.01] border border-white/5 border-dashed mt-12">
                <p className="text-sm text-gray-400 font-medium">Belum ada sinyal yang sesuai filter.</p>
              </div>
            ) : (
              filteredSignals.map((sig, idx) => {
                const isBuy = sig.type.includes("BUY");
                // Outline colors based on the reference: Yellow/Orange for SELL, Green for BUY
                const outlineColor = isBuy ? "border-emerald-500/50" : "border-[#ffaa00]/50";
                const textColor = isBuy ? "text-emerald-400" : "text-[#ff4444]";
                const iconDir = isBuy ? "△" : "▽";
                
                // TP Levels logic (mocking 3 levels based on the main TP)
                const distance = Math.abs(sig.tp - sig.entry);
                const tp1 = isBuy ? sig.entry + distance * 0.33 : sig.entry - distance * 0.33;
                const tp2 = isBuy ? sig.entry + distance * 0.66 : sig.entry - distance * 0.66;
                const tp3 = sig.tp;

                // Status pill
                let statusText = sig.status === 'PENDING' ? 'PENDING' : sig.status === 'ACTIVE' ? 'OPEN' : sig.status === 'WIN' ? 'HIT TP' : 'HIT SL';
                if (sig.status === 'CANCELLED') statusText = 'CLOSED';
                const statusColor = statusText === 'HIT TP' ? (isBuy ? 'text-emerald-400 border-emerald-400' : 'text-[#ff4444] border-[#ff4444]') 
                                  : statusText === 'HIT SL' ? (isBuy ? 'text-[#ff4444] border-[#ff4444]' : 'text-[#ff4444] border-[#ff4444]') 
                                  : 'text-gray-400 border-gray-400';

                return (
                  <div key={idx} className={`bg-[#121215] border ${outlineColor} rounded-2xl p-5 shadow-xl relative overflow-hidden flex flex-col mb-4`}>
                    
                    {/* Header Row */}
                    <div className="flex justify-between items-start mb-3">
                      <div>
                        <h4 className={`text-lg font-medium tracking-wide ${textColor}`}>
                          {iconDir} {sig.type.replace(" LIMIT", "")} {sig.symbol.replace("/", "")}
                        </h4>
                        <div className="inline-flex items-center space-x-1 bg-white/5 border border-yellow-500/30 px-2 py-0.5 rounded text-[10px] text-yellow-500 font-bold mt-1">
                          <span>★ ALL</span>
                        </div>
                      </div>
                      
                      {/* Status Pill */}
                      <div className={`px-3 py-1 rounded-full border text-[10px] font-bold ${statusColor}`}>
                        {statusText}
                      </div>
                    </div>
                    
                    <div className="flex flex-row">
                      {/* Left Side (Grid) */}
                      <div className="flex-1 space-y-2 mt-2">
                        <div className="grid grid-cols-[50px_1fr] items-center">
                          <span className="text-gray-400 text-xs">Entry</span>
                          <span className="text-gray-200 text-sm font-mono text-right">{sig.entry.toFixed(2)}</span>
                        </div>
                        <div className="w-full h-px bg-white/5"></div>
                        <div className="grid grid-cols-[50px_1fr] items-center">
                          <span className="text-gray-400 text-xs">SL</span>
                          <span className="text-[#ff4444] text-sm font-mono text-right">{sig.sl.toFixed(2)}</span>
                        </div>
                        <div className="w-full h-px bg-white/5"></div>
                        <div className="grid grid-cols-[50px_1fr] items-center">
                          <span className="text-gray-400 text-xs">TP1</span>
                          <span className="text-emerald-400 text-sm font-mono text-right">{tp1.toFixed(2)}</span>
                        </div>
                        <div className="w-full h-px bg-white/5"></div>
                        <div className="grid grid-cols-[50px_1fr] items-center">
                          <span className="text-gray-400 text-xs">TP2</span>
                          <span className="text-emerald-400 text-sm font-mono text-right">{tp2.toFixed(2)}</span>
                        </div>
                        <div className="w-full h-px bg-white/5"></div>
                        <div className="grid grid-cols-[50px_1fr] items-center">
                          <span className="text-gray-400 text-xs">TP3</span>
                          <span className="text-emerald-400 text-sm font-mono text-right">{tp3.toFixed(2)}</span>
                        </div>
                      </div>
                      
                      {/* Right Side (Graphic) */}
                      <div className="w-[120px] flex items-center justify-center pl-4 relative">
                         {isBuy ? (
                            <svg width="80" height="120" viewBox="0 0 80 120" fill="none" xmlns="http://www.w3.org/2000/svg">
                              <path d="M10 100 L70 20" stroke="#10B981" strokeWidth="1" strokeDasharray="2 2" opacity="0.3"/>
                              {[...Array(6)].map((_, i) => (
                                <g key={i} transform={`translate(${10 + i * 12}, ${90 - i * 14})`}>
                                  <line x1="2" y1="-8" x2="2" y2="15" stroke="#10B981" strokeWidth="1" />
                                  <rect x="0" y="0" width="4" height="10" fill="#10B981" />
                                </g>
                              ))}
                            </svg>
                         ) : (
                            <svg width="80" height="120" viewBox="0 0 80 120" fill="none" xmlns="http://www.w3.org/2000/svg">
                              <path d="M10 20 L70 100" stroke="#ff4444" strokeWidth="1" opacity="0.3"/>
                              <path d="M10 10 Q 40 10, 70 90" stroke="#ff4444" strokeWidth="1" opacity="0.5" fill="none"/>
                              {[...Array(7)].map((_, i) => (
                                <g key={i} transform={`translate(${10 + i * 10}, ${20 + i * 10 + (i * i * 0.5)})`}>
                                  <line x1="2" y1="-5" x2="2" y2="18" stroke="#ff4444" strokeWidth="1" />
                                  <rect x="0" y="0" width="5" height="12" fill="#ff4444" />
                                </g>
                              ))}
                            </svg>
                         )}
                      </div>
                    </div>
                    
                    {/* Bottom Info */}
                    <div className="mt-4 pt-3 border-t border-white/5 flex items-center flex-wrap gap-1">
                      {sig.reasons && sig.reasons.length > 0 ? (
                        <p className="text-[10px] text-[#ffaa00] font-medium leading-relaxed">
                          Risk medium • {sig.reasons.slice(0, 2).join(" • ")} {sig.reasons.length > 2 ? ` • ${sig.reasons[2]}` : ""}
                        </p>
                      ) : (
                        <p className="text-[10px] text-[#ffaa00] font-medium">Risk medium • System Generated</p>
                      )}
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
      ${active ? 'bg-purple-600 text-white shadow-[0_0_15px_rgba(168,85,247,0.3)] border border-purple-500' : 'text-gray-500 hover:text-white hover:bg-white/5'}`}>
      {icon}
      
      {/* Tooltip */}
      <span className="absolute left-16 px-3 py-1.5 bg-black/80 backdrop-blur-md text-white text-xs font-bold tracking-wide rounded-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 border border-white/10 pointer-events-none">
        {tooltip}
      </span>
    </button>
  );
}

function StatsCard({ title, value, trend, icon, highlight }: { title: string, value: string, trend?: string, icon?: React.ReactNode, highlight: 'emerald' | 'blue' }) {
  const glowColor = highlight === 'emerald' ? 'group-hover:bg-purple-500/10' : 'group-hover:bg-indigo-500/10';
  const textColor = highlight === 'emerald' ? 'group-hover:text-purple-400' : 'group-hover:text-indigo-400';

  return (
    <div className={`bg-white/[0.02] backdrop-blur-md border border-white/5 rounded-[24px] p-6 hover:bg-white/[0.04] hover:border-white/10 hover:-translate-y-1 transition-all duration-500 relative overflow-hidden group flex flex-col justify-between h-full shadow-lg`}>
      <div className={`absolute top-0 right-0 w-32 h-32 rounded-full blur-[40px] -mr-10 -mt-10 transition-colors duration-500 ${glowColor}`}></div>
      
      <div className="flex justify-between items-start relative z-10">
        <p className="text-gray-400 text-sm font-semibold tracking-wider uppercase">{title}</p>
        {icon && <div className="p-2 bg-white/5 rounded-xl">{icon}</div>}
      </div>
      
      <div className="flex items-end space-x-3 relative z-10 mt-4">
        <h3 className={`text-4xl font-black text-white font-mono tracking-tighter transition-colors duration-500 ${textColor}`}>{value}</h3>
        {trend && <span className="text-xs text-purple-400 bg-purple-500/10 px-2 py-1 rounded-md font-bold border border-purple-500/20 mb-1">{trend}</span>}
      </div>
    </div>
  );
}
