"use client";

import TradingChart from "@/components/TradingChart";
import { Activity, Bell, Settings, Target, TrendingUp, History, Zap, Search, LayoutDashboard } from "lucide-react";
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
  status: string;
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
  const [sidebarOpen, setSidebarOpen] = useState(false);

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
          setSignals(prev => {
            const exists = prev.find(s => s.symbol === payload.signal.symbol && s.timestamp === payload.signal.timestamp);
            if (exists) return prev;
            return [payload.signal, ...prev].slice(0, 50);
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

  const filteredSignals = signals.filter(sig => {
    if (filter === "All") return true;
    if (filter === "Open") return sig.status === "PENDING" || sig.status === "ACTIVE";
    if (filter === "Hit TP") return sig.status === "WIN";
    if (filter === "Hit SL") return sig.status === "LOSS";
    if (filter === "Closed") return sig.status === "WIN" || sig.status === "LOSS" || sig.status === "CANCELLED";
    return true;
  });

  return (
    <div className="flex h-screen overflow-hidden bg-slate-900 text-slate-200 font-sans selection:bg-indigo-500/30">
      
      {/* Toasts / Notifications */}
      <div className="fixed top-6 right-6 z-50 flex flex-col space-y-3 pointer-events-none">
        {toasts.map(toast => (
          <div key={toast.id} className={`p-4 rounded-sm shadow-lg border backdrop-blur-md flex items-center space-x-4 transition-all duration-300 ${toast.status === 'WIN' ? 'bg-emerald-500/10 border-emerald-500/50 text-emerald-500' : 'bg-rose-500/10 border-rose-500/50 text-rose-500'}`}>
            <div className="pr-2">
              <p className="font-semibold tracking-wide">{toast.symbol} {toast.status === 'WIN' ? 'Hit Take Profit! 🎯' : 'Hit Stop Loss 🛑'}</p>
              <p className="text-sm font-medium mt-0.5">{toast.tradeType} • PnL: <span>{toast.pnl > 0 ? '+' : ''}{toast.pnl}R</span></p>
            </div>
          </div>
        ))}
      </div>

      {/* Settings Modal */}
      {showSettings && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/80 backdrop-blur-sm p-4">
          <div className="bg-slate-800 border border-slate-700 p-6 rounded-sm w-full max-w-md shadow-2xl relative">
            <h2 className="text-xl font-semibold text-slate-100 mb-6">System Configuration</h2>
            <form onSubmit={saveSettings} className="space-y-4">
              <div>
                <label className="block text-slate-400 text-sm font-medium mb-1">Account Balance ($)</label>
                <input 
                  type="number" 
                  step="0.01"
                  className="w-full bg-slate-900 border border-slate-700 rounded-sm px-3 py-2 text-slate-200 focus:outline-none focus:border-indigo-500 transition-colors"
                  value={settings.account_balance}
                  onChange={(e) => setSettings({...settings, account_balance: parseFloat(e.target.value) || 0})}
                />
              </div>
              <div>
                <label className="block text-slate-400 text-sm font-medium mb-1">Risk per Trade (%)</label>
                <input 
                  type="number" 
                  step="0.1"
                  className="w-full bg-slate-900 border border-slate-700 rounded-sm px-3 py-2 text-slate-200 focus:outline-none focus:border-indigo-500 transition-colors"
                  value={settings.risk_percentage}
                  onChange={(e) => setSettings({...settings, risk_percentage: parseFloat(e.target.value) || 0})}
                />
              </div>
              <div className="flex justify-end space-x-3 pt-4 mt-6 border-t border-slate-700">
                <button type="button" onClick={() => setShowSettings(false)} className="px-4 py-2 rounded-sm text-slate-400 hover:text-slate-200 transition-colors">Cancel</button>
                <button type="submit" className="px-4 py-2 rounded-sm bg-indigo-500 hover:bg-indigo-600 text-white font-medium transition-colors">Save Changes</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Sidebar */}
      <div className={`fixed inset-0 bg-slate-900/80 z-40 lg:hidden lg:z-auto transition-opacity duration-200 ${sidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`} aria-hidden="true"></div>
      <aside className={`absolute z-40 left-0 top-0 lg:static lg:left-auto lg:top-auto lg:translate-x-0 h-screen overflow-y-scroll lg:overflow-y-auto no-scrollbar w-64 lg:w-20 xl:w-64 flex flex-col bg-slate-800 border-r border-slate-700 transition-transform duration-200 ease-in-out ${sidebarOpen ? 'translate-x-0' : '-translate-x-64'}`}>
        {/* Sidebar Header */}
        <div className="flex justify-between mb-10 pr-3 sm:px-2 mt-4 lg:justify-center xl:justify-start xl:px-4">
          <div className="flex items-center space-x-2 pl-4 lg:pl-0 xl:pl-0">
            <div className="w-8 h-8 rounded bg-indigo-500 flex items-center justify-center">
              <Zap size={18} className="text-white" />
            </div>
            <span className="text-slate-100 font-semibold text-lg lg:hidden xl:block">Novaire EA</span>
          </div>
          <button className="lg:hidden text-slate-400 hover:text-slate-200" onClick={() => setSidebarOpen(false)}>
             <svg className="w-6 h-6 fill-current" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                <path d="M10.7 18.7l1.4-1.4L7.8 13H20v-2H7.8l4.3-4.3-1.4-1.4L4 12z" />
             </svg>
          </button>
        </div>

        {/* Links */}
        <div className="space-y-8">
          <div>
            <h3 className="text-xs uppercase text-slate-500 font-semibold pl-4 lg:hidden xl:block mb-3">Pages</h3>
            <ul className="mt-3 space-y-1">
              <NavItem icon={<LayoutDashboard size={20} />} label="Dashboard" active />
              <NavItem icon={<TrendingUp size={20} />} label="Signals" />
              <NavItem icon={<History size={20} />} label="Performance" />
            </ul>
          </div>
          <div>
            <h3 className="text-xs uppercase text-slate-500 font-semibold pl-4 lg:hidden xl:block mb-3">Settings</h3>
            <ul className="mt-3 space-y-1">
              <NavItem icon={<Settings size={20} />} label="System Config" onClick={() => setShowSettings(true)} />
            </ul>
          </div>
        </div>
      </aside>

      {/* Main Container */}
      <div className="relative flex flex-col flex-1 overflow-y-auto overflow-x-hidden">
        
        {/* Header */}
        <header className="sticky top-0 bg-slate-900 border-b border-slate-700 z-30">
          <div className="px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-16 -mb-px">
              
              <div className="flex items-center">
                <button className="text-slate-500 hover:text-slate-400 lg:hidden" onClick={() => setSidebarOpen(true)}>
                  <svg className="w-6 h-6 fill-current" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <rect x="4" y="5" width="16" height="2" />
                    <rect x="4" y="11" width="16" height="2" />
                    <rect x="4" y="17" width="16" height="2" />
                  </svg>
                </button>
                <div className="hidden sm:block ml-4 text-slate-100 font-semibold text-xl">Novaire EA</div>
              </div>
              
              <div className="flex items-center space-x-3">
                {/* Search (Mock) */}
                <div className="hidden sm:block bg-slate-800 rounded-sm border border-slate-700 px-3 py-1.5 flex items-center space-x-2 text-slate-400">
                   <Search size={16} />
                   <span className="text-sm">Search signals...</span>
                </div>
                
                {/* Symbol Tabs */}
                <div className="flex bg-slate-800 rounded-sm border border-slate-700 p-1">
                  {['XAU/USD'].map((pair) => (
                    <button 
                      key={pair} 
                      onClick={() => setActiveSymbol(pair)}
                      className={`px-3 py-1 text-sm rounded-sm transition-colors ${activeSymbol === pair ? 'bg-indigo-500 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}>
                      {pair}
                    </button>
                  ))}
                </div>
                
                <hr className="w-px h-6 bg-slate-700 mx-2 hidden sm:block" />
                
                {/* Notifications */}
                <button className="w-8 h-8 flex items-center justify-center bg-slate-800 border border-slate-700 hover:border-slate-600 rounded-sm transition-colors text-slate-400 relative">
                  <Bell size={16} />
                  <div className="absolute top-0 right-0 w-2.5 h-2.5 bg-rose-500 border-2 border-slate-900 rounded-full"></div>
                </button>
              </div>
            </div>
          </div>
        </header>

        <main>
          <div className="px-4 sm:px-6 lg:px-8 py-8 w-full max-w-9xl mx-auto">
            
            {/* Top Cards */}
            <div className="grid grid-cols-12 gap-6">
              <StatsCard colSpan="col-span-12 sm:col-span-6 xl:col-span-4" title="Monthly Winrate" value={`${stats.win_rate}%`} trend={stats.total_trades} />
              <StatsCard colSpan="col-span-12 sm:col-span-6 xl:col-span-4" title="Active Protocols" value="3" highlight />
              <StatsCard colSpan="col-span-12 sm:col-span-6 xl:col-span-4" title="Engine Status" value="Online" status="good" />
            </div>

            {/* Trading Chart */}
            <div className="mt-6 bg-slate-800 shadow-lg rounded-sm border border-slate-700">
              <header className="px-5 py-4 border-b border-slate-700">
                <h2 className="font-semibold text-slate-100">Live Analysis: {activeSymbol}</h2>
              </header>
              <div className="p-1 h-[350px] sm:h-[500px]">
                <TradingChart symbol={activeSymbol} />
              </div>
            </div>

            {/* Signal Data Table */}
            <div className="mt-6 bg-slate-800 shadow-lg rounded-sm border border-slate-700">
              <header className="px-5 py-4 border-b border-slate-700 flex justify-between items-center flex-wrap gap-2">
                <h2 className="font-semibold text-slate-100">Signals Queue <span className="text-slate-500 font-medium ml-1">{signals.length}</span></h2>
                
                {/* Filter Pills */}
                <div className="flex space-x-2 text-sm">
                  {['All', 'Open', 'Hit TP', 'Hit SL', 'Closed'].map(f => (
                    <button
                      key={f}
                      onClick={() => setFilter(f)}
                      className={`px-3 py-1 rounded-full transition-colors ${filter === f ? 'bg-indigo-500 text-white' : 'text-slate-400 bg-slate-700/50 hover:bg-slate-700 hover:text-slate-200'}`}
                    >
                      {f}
                    </button>
                  ))}
                </div>
              </header>
              
              <div className="p-3">
                <div className="overflow-x-auto">
                  <table className="table-auto w-full text-slate-300">
                    <thead className="text-xs uppercase text-slate-500 bg-slate-900/50 rounded-sm">
                      <tr>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-left">Signal</div></th>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-left">Grade</div></th>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-right">Entry</div></th>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-right">Stop Loss</div></th>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-right">Target</div></th>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-center">Status</div></th>
                        <th className="p-3 whitespace-nowrap"><div className="font-semibold text-left">Context</div></th>
                      </tr>
                    </thead>
                    <tbody className="text-sm divide-y divide-slate-700/50">
                      {filteredSignals.length === 0 ? (
                        <tr>
                          <td colSpan={7} className="p-8 text-center text-slate-500">No signals found for this filter.</td>
                        </tr>
                      ) : (
                        filteredSignals.map((sig, idx) => {
                          const isBuy = sig.type.includes("BUY");
                          const typeColor = isBuy ? "text-emerald-500" : "text-rose-500";
                          const typeBg = isBuy ? "bg-emerald-500/10" : "bg-rose-500/10";
                          
                          let statusText = sig.status === 'PENDING' ? 'PENDING' : sig.status === 'ACTIVE' ? 'OPEN' : sig.status === 'WIN' ? 'HIT TP' : 'HIT SL';
                          if (sig.status === 'CANCELLED') statusText = 'CLOSED';
                          
                          let statusColor = "bg-slate-700 text-slate-300";
                          if (statusText === 'HIT TP') statusColor = "bg-emerald-500/20 text-emerald-400";
                          if (statusText === 'HIT SL') statusColor = "bg-rose-500/20 text-rose-400";
                          if (statusText === 'OPEN') statusColor = "bg-indigo-500/20 text-indigo-400";

                          return (
                            <tr key={idx} className="hover:bg-slate-700/20 transition-colors">
                              <td className="p-3 whitespace-nowrap">
                                <div className="flex items-center">
                                  <div className={`w-8 h-8 rounded-full flex items-center justify-center mr-3 ${typeBg} ${typeColor}`}>
                                    {isBuy ? '↑' : '↓'}
                                  </div>
                                  <div className="font-medium text-slate-100">{sig.type} {sig.symbol.replace("/", "")}</div>
                                </div>
                              </td>
                              <td className="p-3 whitespace-nowrap">
                                <div className="text-left font-medium text-amber-500">{(sig as any).grade || 'A'}</div>
                              </td>
                              <td className="p-3 whitespace-nowrap">
                                <div className="text-right text-slate-200 font-mono">{sig.entry.toFixed(2)}</div>
                              </td>
                              <td className="p-3 whitespace-nowrap">
                                <div className="text-right text-rose-400 font-mono">{sig.sl.toFixed(2)}</div>
                              </td>
                              <td className="p-3 whitespace-nowrap">
                                <div className="text-right text-emerald-400 font-mono">{sig.tp.toFixed(2)}</div>
                              </td>
                              <td className="p-3 whitespace-nowrap">
                                <div className="text-center">
                                  <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${statusColor}`}>{statusText}</span>
                                </div>
                              </td>
                              <td className="p-3 max-w-xs truncate">
                                <div className="text-left text-slate-400 text-xs truncate" title={sig.reasons.join(" • ")}>
                                  {sig.reasons.join(" • ")}
                                </div>
                              </td>
                            </tr>
                          )
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

          </div>
        </main>
      </div>
    </div>
  );
}

// Subcomponents
function NavItem({ icon, label, active, onClick }: { icon: React.ReactNode, label: string, active?: boolean, onClick?: () => void }) {
  return (
    <li>
      <button onClick={onClick} className={`w-full flex items-center px-4 py-2 transition-colors lg:justify-center xl:justify-start ${active ? 'text-indigo-400 bg-slate-900/50 relative' : 'text-slate-400 hover:text-slate-200'}`}>
        {active && <div className="absolute left-0 top-0 bottom-0 w-1 bg-indigo-500"></div>}
        <span className="shrink-0">{icon}</span>
        <span className="ml-3 font-medium text-sm lg:hidden xl:block">{label}</span>
      </button>
    </li>
  );
}

function StatsCard({ colSpan, title, value, trend, status, highlight }: { colSpan: string, title: string, value: string, trend?: number, status?: 'good' | 'bad', highlight?: boolean }) {
  return (
    <div className={`flex flex-col ${colSpan} bg-slate-800 shadow-lg rounded-sm border border-slate-700 p-5 relative overflow-hidden`}>
      {highlight && <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/10 blur-[40px] rounded-full -mr-10 -mt-10"></div>}
      <h2 className="text-slate-400 text-sm font-semibold mb-2">{title}</h2>
      <div className="flex items-start">
        <div className="text-3xl font-bold text-slate-100 mr-2">{value}</div>
        {trend !== undefined && (
          <div className="text-sm font-medium text-emerald-500 bg-emerald-500/10 px-1.5 py-0.5 rounded-full mt-1">
             {trend} trades
          </div>
        )}
        {status === 'good' && (
           <div className="flex items-center space-x-1 mt-1.5 text-emerald-500 text-sm font-medium">
             <div className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></div>
             <span>Active</span>
           </div>
        )}
      </div>
    </div>
  );
}

