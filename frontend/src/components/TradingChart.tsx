"use client";

import React, { useEffect, useState, useRef } from "react";
import dynamic from "next/dynamic";

const Chart = dynamic(() => import("react-apexcharts"), { ssr: false });

interface ChartProps {
  symbol: string;
}

export default function TradingChart({ symbol }: ChartProps) {
  return (
    <ErrorBoundary>
      <TradingChartInner symbol={symbol} />
    </ErrorBoundary>
  );
}

class ErrorBoundary extends React.Component<any, { hasError: boolean, errorMsg: string }> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, errorMsg: "" };
  }
  static getDerivedStateFromError(error: any) {
    return { hasError: true, errorMsg: error.toString() };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="w-full h-[550px] flex items-center justify-center bg-black/40 backdrop-blur-3xl rounded-[32px] border border-rose-500/20">
          <p className="text-rose-400 font-medium">Grafik Crash: {this.state.errorMsg}</p>
        </div>
      );
    }
    return this.props.children;
  }
}

function TradingChartInner({ symbol }: ChartProps) {
  const [loading, setLoading] = useState(true);
  const [series, setSeries] = useState<any[]>([{ data: [] }]);
  const [error, setError] = useState<string | null>(null);
  
  const dataRef = useRef<any[]>([]);

  useEffect(() => {
    let ws: WebSocket;
    let isMounted = true;

    const initData = async () => {
      try {
        setLoading(true);
        setError(null);
        const encodedSymbol = encodeURIComponent(symbol.replace("/", "")); // Twelvedata often uses XAU/USD in backend but REST might want XAU/USD directly
        // The backend expects "XAU/USD" literally.
        const res = await fetch(`http://localhost:8000/api/historical/${encodeURIComponent(symbol)}`);
        const json = await res.json();
        
        if (json.data && json.data.length > 0 && isMounted) {
          const formatted = json.data.map((d: any) => {
            const timeValue = typeof d.time === 'number' ? d.time * 1000 : d.time;
            return {
              x: new Date(timeValue).getTime(),
              y: [d.open, d.high, d.low, d.close]
            };
          });
          dataRef.current = formatted;
          setSeries([{ data: formatted }]);
        }
        if (isMounted) setLoading(false);

        // Connect WS
        ws = new WebSocket("ws://localhost:8000/ws");
        ws.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            if (payload.type === "TICK" && payload.data.symbol === symbol) {
              const tickPrice = payload.data.price;
              
              if (dataRef.current.length > 0 && isMounted) {
                // Update the last candle with new tick data dynamically
                const lastCandle = { ...dataRef.current[dataRef.current.length - 1] };
                const [open, high, low, close] = lastCandle.y;
                
                lastCandle.y = [
                  open, 
                  Math.max(high, tickPrice), 
                  Math.min(low, tickPrice), 
                  tickPrice
                ];
                
                dataRef.current[dataRef.current.length - 1] = lastCandle;
                setSeries([{ data: [...dataRef.current] }]);
              }
            }
          } catch (e) {}
        };
      } catch (err) {
        if (isMounted) {
          setError("Koneksi ke server tertunda.");
          setLoading(false);
        }
      }
    };

    initData();

    return () => {
      isMounted = false;
      if (ws) ws.close();
    };
  }, [symbol]);

  const options: any = {
    chart: {
      type: 'candlestick',
      background: 'transparent',
      toolbar: { show: false },
      animations: { enabled: true, dynamicAnimation: { speed: 300 } }
    },
    theme: { mode: 'dark' },
    grid: {
      show: false, // Removes stiff grid lines
      padding: { top: 0, right: 0, bottom: 0, left: 10 }
    },
    plotOptions: {
      candlestick: {
        colors: { upward: '#10B981', downward: '#F43F5E' },
        wick: { useFillColor: true }
      }
    },
    xaxis: {
      type: 'datetime',
      labels: { style: { colors: '#6B7280', fontFamily: 'var(--font-mono)' } },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false }
    },
    yaxis: {
      tooltip: { enabled: true },
      labels: {
        style: { colors: '#6B7280', fontFamily: 'var(--font-mono)' },
        formatter: (value: number) => { return typeof value === 'number' ? value.toFixed(2) : "" }
      }
    }
  };

  return (
    <div className="relative w-full h-[550px] flex flex-col bg-black/40 backdrop-blur-3xl rounded-[32px] border border-white/5 overflow-hidden shadow-2xl group">
      
      {/* Floating Header */}
      <div className="absolute top-6 left-6 right-6 z-10 flex justify-between items-center pointer-events-none">
        <div className="flex items-center space-x-5 pointer-events-auto bg-black/60 backdrop-blur-xl px-5 py-3 rounded-2xl border border-white/10 shadow-lg">
          <div>
             <h2 className="text-3xl font-black text-white tracking-tighter font-display drop-shadow-[0_0_15px_rgba(255,255,255,0.3)]">{symbol}</h2>
          </div>
          <div className="h-8 w-px bg-white/10"></div>
          <div className="flex flex-col">
            <span className="text-[10px] text-gray-400 font-bold uppercase tracking-[0.2em]">Realtime</span>
            <span className="text-sm font-medium text-emerald-400">Smart Money Concept</span>
          </div>
        </div>
        
        <div className="flex items-center space-x-3 pointer-events-auto bg-emerald-500/10 backdrop-blur-xl px-5 py-3 rounded-2xl border border-emerald-500/20 shadow-[0_0_30px_rgba(16,185,129,0.15)]">
          <div className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
          </div>
          <span className="text-emerald-400 font-mono text-sm tracking-widest font-bold">CONNECTED</span>
        </div>
      </div>

      {loading ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center">
          <div className="w-16 h-16 border-4 border-white/5 border-t-emerald-500 rounded-full animate-spin"></div>
        </div>
      ) : error ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center">
          <p className="text-rose-400 font-medium bg-rose-500/10 px-6 py-3 rounded-xl border border-rose-500/20">{error}</p>
        </div>
      ) : (
        <div className="w-full flex-grow mt-28 px-4 pb-6 pointer-events-auto z-10 relative">
          <Chart options={options} series={series} type="candlestick" height="100%" />
        </div>
      )}
      
      {/* Dynamic Orbs */}
      <div className="absolute -bottom-40 -right-40 w-[500px] h-[500px] bg-emerald-500/10 rounded-full blur-[100px] pointer-events-none group-hover:scale-110 transition-transform duration-1000"></div>
      <div className="absolute -top-40 -left-40 w-[500px] h-[500px] bg-blue-500/10 rounded-full blur-[100px] pointer-events-none group-hover:scale-110 transition-transform duration-1000"></div>
    </div>
  );
}
