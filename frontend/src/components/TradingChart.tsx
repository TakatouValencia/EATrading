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
        <div className="w-full h-full flex items-center justify-center bg-slate-800 rounded-sm">
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
        const encodedSymbol = encodeURIComponent(symbol.replace("/", ""));
        const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        const res = await fetch(`${API_URL}/api/historical/${encodeURIComponent(symbol)}`);
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
        const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
        ws = new WebSocket(`${WS_URL}/ws`);
        ws.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            if (payload.type === "TICK" && payload.data.symbol === symbol) {
              const tickPrice = payload.data.price;
              
              if (dataRef.current.length > 0 && isMounted) {
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
      show: true,
      borderColor: '#334155', // slate-700
      strokeDashArray: 4,
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
      labels: { style: { colors: '#94a3b8', fontFamily: 'inherit' } },
      axisBorder: { show: false },
      axisTicks: { show: false },
      tooltip: { enabled: false }
    },
    yaxis: {
      tooltip: { enabled: true },
      labels: {
        style: { colors: '#94a3b8', fontFamily: 'inherit' },
        formatter: (value: number) => { return typeof value === 'number' ? value.toFixed(2) : "" }
      }
    }
  };

  return (
    <div className="relative w-full h-full flex flex-col bg-transparent">
      {loading ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center">
           <div className="w-8 h-8 border-2 border-slate-700 border-t-indigo-500 rounded-full animate-spin"></div>
        </div>
      ) : error ? (
        <div className="absolute inset-0 z-20 flex items-center justify-center">
          <p className="text-rose-500 font-medium bg-rose-500/10 px-4 py-2 rounded-sm border border-rose-500/20">{error}</p>
        </div>
      ) : (
        <div className="w-full h-full relative z-10 px-2 py-4">
          {/* Status Indicator */}
          <div className="absolute top-2 left-4 z-20 flex items-center space-x-2 bg-slate-800/80 px-2.5 py-1 rounded-sm border border-slate-700">
            <div className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </div>
            <span className="text-emerald-500 font-mono text-[10px] tracking-widest font-bold">LIVE</span>
          </div>
          <Chart options={options} series={series} type="candlestick" height="100%" />
        </div>
      )}
    </div>
  );
}
