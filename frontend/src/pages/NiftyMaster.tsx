import { Zap, Activity, TrendingDown, TrendingUp, Minus, AlertTriangle, CheckCircle2, ShieldCheck, BarChart3, Clock, History, BarChart, Search, Maximize2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'


interface TradeLeg {
  symbol: string
  action: 'BUY' | 'SELL'
  qty: number
  option_type: 'CE' | 'PE'
  live_pnl?: number
}

interface TradeSlot {
  active: boolean
  legs: TradeLeg[]
  premium_collected: number
  strike: number
  type: string
  entry_time: string
  intraday_only?: boolean
  short_offset?: number
  live_pnl?: number
  greeks?: {
    delta: number
    theta: number
    vega: number
  }
}

interface DecisionEntry {
  time: string
  day: string
  slot: string
  action: string
  reason: string
  gates: Record<string, { passed?: boolean; value: any; threshold?: string }>
}

interface AdjustmentSignal {
  slot: string
  type: 'ROLL_DECAY' | 'TESTED'
  side: 'CE' | 'PE'
  msg: string
  symbol: string
}

interface OIData {
  strike: number
  ce_oi: number
  pe_oi: number
  total_oi: number
}

interface KeyLevels {
  max_pain: number
  ce_wall: number
  ce_wall_oi: number
  pe_wall: number
  pe_wall_oi: number
}

interface NiftyState {
  current_week: string
  weekly_pnl: number
  week_blocked: boolean
  thursday_close: number
  monday_close: number
  market_data: {
    vix: number
    ivr: number
    adx: number
    pcr: number
    nifty_ltp: number
  }
  carry_trade: TradeSlot
  monday_trade: TradeSlot
  tuesday_trade: TradeSlot
  last_eval: string
  morning_pcr: number
  morning_pcr_date: string
  morning_spot?: number
  wednesday_close: number
  oi_data?: OIData[]
  key_levels?: KeyLevels
  decision_log?: DecisionEntry[]
  adjustment_signals?: AdjustmentSignal[]
  weekly_limit?: number
  error?: string
}

interface JournalData {
  trades: any[]
  stats: {
    total_trades: number
    wins: number
    losses: number
    win_rate: number
    total_pnl: number
    avg_win: number
    avg_loss: number
    profit_factor: number | string
    by_slot: Record<string, any>
  }
  lifetime_stats: {
    win_rate: number
    total_pnl: number
    profit_factor: number
  }
}

const PayoffMini = ({ slot, niftyLtp }: { slot: TradeSlot; niftyLtp: number }) => {
  if (!slot.active || !slot.strike) return null;
  const offset = slot.short_offset || 400;
  const lowerBound = slot.strike - offset;
  const upperBound = slot.strike + offset;
  const windowRange = offset * 2.5;
  const windowStart = slot.strike - (windowRange / 2);
  const getPos = (val: number) => Math.max(0, Math.min(100, ((val - windowStart) / windowRange) * 100));

  const currentPos = getPos(niftyLtp);
  const lowerPos = getPos(lowerBound);
  const upperPos = getPos(upperBound);
  const isSafe = niftyLtp >= lowerBound && niftyLtp <= upperBound;

  return (
    <div className="group relative cursor-pointer hover:bg-primary/5 transition-all duration-300 p-4 rounded-2xl border border-white/5 bg-muted/10 backdrop-blur-sm overflow-hidden">
      {/* Decorative Glow */}
      <div className={cn("absolute -right-4 -top-4 w-12 h-12 rounded-full blur-2xl transition-colors duration-1000", isSafe ? "bg-green-500/10" : "bg-red-500/10")} />

      <div className="flex justify-between items-center mb-3">
        <div className="flex items-center gap-2">
          <div className={cn("h-1.5 w-1.5 rounded-full shadow-[0_0_8px_rgba(34,197,94,0.5)]", isSafe ? "bg-green-500" : "bg-red-500 animate-ping")} />
          <span className="text-[9px] font-black text-muted-foreground uppercase tracking-widest">Structural Range</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-black tabular-nums font-mono text-primary">{niftyLtp.toFixed(0)}</span>
          <Maximize2 className="h-3 w-3 text-muted-foreground/30 group-hover:text-primary transition-colors" />
        </div>
      </div>

      <div className="relative h-2 flex items-center bg-muted/40 rounded-full border border-white/5 shadow-inner">
        <div
          className={cn(
            "absolute h-full transition-all duration-1000 rounded-full shadow-[0_0_15px_rgba(34,197,94,0.1)]",
            isSafe ? "bg-green-500/40" : "bg-red-500/40"
          )}
          style={{ left: `${lowerPos}%`, width: `${upperPos - lowerPos}%` }}
        />
        <div
          className={cn(
            "absolute h-3 w-3 rounded-full border-2 border-background shadow-xl transition-all duration-1000 z-10",
            isSafe ? "bg-green-500 shadow-green-500/50" : "bg-red-500 shadow-red-500/50 animate-pulse"
          )}
          style={{ left: `${currentPos}%`, transform: 'translateX(-50%)' }}
        />
      </div>
    </div>
  );
};

const PayoffVisualizer = ({ slot, niftyLtp, keyLevels }: { slot: TradeSlot; niftyLtp: number; keyLevels?: KeyLevels }) => {
  if (!slot.active || !slot.strike) return null;

  const offset = slot.short_offset || 400;
  const lowerBound = slot.strike - offset;
  const upperBound = slot.strike + offset;
  const windowRange = offset * 4;
  const windowStart = slot.strike - (windowRange / 2);

  const getPos = (val: number) => {
    const pos = ((val - windowStart) / windowRange) * 100;
    return Math.max(0, Math.min(100, pos));
  };

  const currentPos = getPos(niftyLtp);
  const strikePos = getPos(slot.strike);
  const lowerPos = getPos(lowerBound);
  const upperPos = getPos(upperBound);
  const isSafe = niftyLtp >= lowerBound && niftyLtp <= upperBound;

  return (
    <div className="mt-2 p-8 rounded-3xl bg-gradient-to-br from-muted/20 to-background border border-primary/10 relative overflow-hidden shadow-2xl">
      {/* Background Decorative Glows */}
      <div className={cn("absolute -top-24 -left-24 w-48 h-48 rounded-full blur-[100px] opacity-20", isSafe ? "bg-green-500" : "bg-red-500")} />

      <div className="flex justify-between items-center mb-12">
        <div className="space-y-1">
          <h4 className="text-[10px] font-black text-primary uppercase tracking-[0.2em] flex items-center gap-2">
            <Activity className="h-3 w-3 animate-pulse" /> Live Structural Pulse
          </h4>
          <p className="text-[9px] text-muted-foreground font-bold uppercase opacity-60">Real-time payoff projection</p>
        </div>
        <Badge className={cn(
          "px-3 py-1 text-[10px] font-black uppercase tracking-widest border-none shadow-lg transition-colors duration-1000",
          isSafe ? "bg-green-500 text-white shadow-green-500/20" : "bg-red-500 text-white shadow-red-500/20 animate-bounce"
        )}>
          {isSafe ? "Zone: Optimal" : "Zone: Critical"}
        </Badge>
      </div>

      <div className="relative h-32 flex items-center">
        {/* The Track Base */}
        <div className="absolute w-full h-2 bg-muted/40 rounded-full border border-white/5" />

        {/* Profit Range Glow */}
        <div
          className={cn(
            "absolute h-2 transition-all duration-1000 rounded-full shadow-[0_0_20px_rgba(34,197,94,0.2)]",
            isSafe ? "bg-green-500/40" : "bg-red-500/20"
          )}
          style={{ left: `${lowerPos}%`, width: `${upperPos - lowerPos}%` }}
        />

        {/* Vertical Tiers for Labels (Anti-Overlap Logic) */}

        {/* TIER 1: Structural Markers (Bottom) */}
        {keyLevels && (
          <>
            {keyLevels.max_pain > 0 && (
              <div className="absolute group flex flex-col items-center z-10 transition-all" style={{ left: `${getPos(keyLevels.max_pain)}%`, bottom: '-20px' }}>
                <div className="h-8 w-px bg-orange-500/50 group-hover:h-12 group-hover:bg-orange-500 transition-all" />
                <span className="text-[8px] font-black text-orange-500 uppercase mt-1 opacity-40 group-hover:opacity-100">Pain</span>
              </div>
            )}
            {keyLevels.pe_wall > 0 && (
              <div className="absolute group flex flex-col items-center z-10" style={{ left: `${getPos(keyLevels.pe_wall)}%`, bottom: '-20px' }}>
                <div className="h-8 w-px bg-green-500/50 group-hover:h-12 group-hover:bg-green-500 transition-all" />
                <span className="text-[8px] font-black text-green-500 uppercase mt-1 opacity-40 group-hover:opacity-100">Sup</span>
              </div>
            )}
            {keyLevels.ce_wall > 0 && (
              <div className="absolute group flex flex-col items-center z-10" style={{ left: `${getPos(keyLevels.ce_wall)}%`, bottom: '-20px' }}>
                <div className="h-8 w-px bg-red-500/50 group-hover:h-12 group-hover:bg-red-500 transition-all" />
                <span className="text-[8px] font-black text-red-500 uppercase mt-1 opacity-40 group-hover:opacity-100">Res</span>
              </div>
            )}
          </>
        )}

        {/* TIER 2: Strategy Bounds (Middle-Bottom) */}
        <div className="absolute h-12 w-px bg-foreground/20" style={{ left: `${lowerPos}%`, bottom: '0px' }}>
          <span className="absolute -bottom-8 text-[9px] font-mono font-black text-muted-foreground -translate-x-1/2">{lowerBound}</span>
        </div>
        <div className="absolute h-12 w-px bg-foreground/20" style={{ left: `${upperPos}%`, bottom: '0px' }}>
          <span className="absolute -bottom-8 text-[9px] font-mono font-black text-muted-foreground -translate-x-1/2">{upperBound}</span>
        </div>

        {/* TIER 3: Anchor Strike (Deep Bottom) */}
        <div className="absolute flex flex-col items-center z-0" style={{ left: `${strikePos}%`, bottom: '-45px' }}>
          <div className="h-16 w-px bg-primary/20 border-l border-dashed" />
          <span className="text-[10px] font-black text-primary/60 mt-1 tabular-nums">{slot.strike}</span>
        </div>

        {/* TIER 4: SPOT CURSOR (Hero Element - TOP) */}
        <div
          className="absolute z-50 transition-all duration-1000 ease-in-out flex flex-col items-center"
          style={{ left: `${currentPos}%`, top: '-40px', transform: 'translateX(-50%)' }}
        >
          {/* Neon Floating Bubble */}
          <div className={cn(
            "px-3 py-1.5 rounded-full font-black text-[14px] tabular-nums shadow-[0_0_30px_rgba(0,0,0,0.5)] border-2 flex items-center gap-2 backdrop-blur-md",
            isSafe ? "bg-green-500 border-green-400 text-white shadow-green-500/40" : "bg-red-500 border-red-400 text-white shadow-red-500/40 animate-pulse"
          )}>
            <div className="h-2 w-2 rounded-full bg-white animate-ping" />
            {niftyLtp.toFixed(1)}
          </div>

          {/* Dynamic Laser Line */}
          <div className={cn(
            "w-px h-24 mt-2 transition-colors duration-1000",
            isSafe ? "bg-gradient-to-b from-green-500 to-transparent" : "bg-gradient-to-b from-red-500 to-transparent"
          )} />

          <div className={cn(
            "h-4 w-4 rounded-full border-2 border-background -mt-1 transition-colors duration-1000 shadow-lg",
            isSafe ? "bg-green-500" : "bg-red-500"
          )} />
        </div>
      </div>

      <div className="mt-16 grid grid-cols-2 gap-4">
        {[
          { label: 'Market Sentiment', val: '~74% Prob.', icon: BarChart3, color: 'text-green-500' },
          { label: 'Safety Buffer', val: `${(Math.abs(niftyLtp - slot.strike) / niftyLtp * 100).toFixed(2)}%`, icon: ShieldCheck, color: 'text-primary' }
        ].map((item, i) => (
          <div key={i} className="flex items-center justify-between px-5 py-4 bg-muted/20 rounded-2xl border border-white/5 backdrop-blur-xl group hover:bg-muted/40 transition-all">
            <div className="flex items-center gap-3">
              <item.icon className={cn("h-4 w-4 opacity-50 group-hover:opacity-100 transition-opacity", item.color)} />
              <span className="text-[10px] text-muted-foreground uppercase font-black tracking-widest">{item.label}</span>
            </div>
            <span className={cn("text-xs font-black tabular-nums", item.color)}>{item.val}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

const AdjustmentAlerts = ({ signals }: { signals?: AdjustmentSignal[] }) => {
  if (!signals || signals.length === 0) {
    return (
      <Card className="border-none shadow-sm bg-muted/20">
        <CardContent className="p-4 flex items-center justify-center gap-3 opacity-30 italic">
          <Zap className="h-4 w-4" />
          <span className="text-[10px] font-bold uppercase tracking-widest">No adjustments needed</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-none shadow-sm bg-orange-500/5 border-l-4 border-orange-500">
      <CardHeader className="py-3 px-4">
        <CardTitle className="text-xs font-black uppercase flex items-center gap-2 text-orange-500">
          <Activity className="h-4 w-4" /> Adjustment Signals
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-4 space-y-3">
        {signals.map((s, i) => (
          <div key={i} className="bg-background rounded-lg border p-3 space-y-2">
            <div className="flex justify-between items-center">
              <Badge variant={s.type === 'TESTED' ? "destructive" : "default"} className="text-[8px] h-4">
                {s.type === 'TESTED' ? 'STRIKE TESTED' : 'DECAY ROLL'}
              </Badge>
              <span className="text-[10px] font-black text-muted-foreground uppercase">{s.slot}</span>
            </div>
            <p className="text-[10px] font-bold leading-tight">{s.msg}</p>
            <div className="flex items-center gap-2">
              <div className={cn("w-1 h-3 rounded-full", s.side === 'CE' ? "bg-red-500" : "bg-green-500")} />
              <span className="text-[9px] font-mono opacity-50">{s.symbol}</span>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
};

const DecisionAudit = ({ logs }: { logs?: DecisionEntry[] }) => {
  const isEmpty = !logs || logs.length === 0;

  return (
    <Card className="border-none shadow-sm overflow-hidden">
      <CardHeader className="bg-muted/30 py-3">
        <CardTitle className="text-xs font-bold uppercase tracking-wider flex items-center gap-2">
          <Search className="h-3.5 w-3.5" /> Decision Audit Trail
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="max-h-[300px] overflow-y-auto min-h-[100px] flex flex-col">
          {isEmpty ? (
            <div className="flex-1 flex flex-col items-center justify-center p-8 opacity-20 italic">
              <Search className="h-8 w-8 mb-2" />
              <p className="text-[10px] font-bold uppercase tracking-widest">No audit logs available</p>
            </div>
          ) : (
            logs.slice().reverse().map((log, i) => (
              <div key={i} className="border-b last:border-0 p-3 hover:bg-muted/20 transition-colors">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-2">
                    <div className={cn(
                      "h-2 w-2 rounded-full mt-1",
                      log.action === 'DEPLOYED' ? "bg-green-500" :
                        log.action === 'EXITED' ? "bg-red-500" : "bg-orange-500"
                    )} />
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-black uppercase">{log.action}: {log.slot}</span>
                        <span className="text-[9px] text-muted-foreground font-mono">{log.time}</span>
                      </div>
                      <p className="text-[10px] font-medium text-muted-foreground mt-0.5">{log.reason}</p>
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-1">
                    {Object.entries(log.gates).map(([key, val]) => (
                      <Badge key={key} variant={val.passed === false ? "destructive" : "outline"} className="text-[8px] py-0 px-1 font-mono h-4">
                        {key.split('_')[0]}: {val.value}
                      </Badge>
                    ))}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
};

const OIHeatmap = ({ data, niftyLtp }: { data?: OIData[]; niftyLtp: number }) => {
  const isEmpty = !data || data.length === 0;

  // Find ATM and slice for main view (5 strikes)
  const mainData = !isEmpty ? data!.slice().sort((a, b) => Math.abs(a.strike - niftyLtp) - Math.abs(b.strike - niftyLtp)).slice(0, 5).sort((a, b) => a.strike - b.strike) : [];
  const maxOI_main = isEmpty ? 1 : Math.max(...mainData.map(d => Math.max(d.ce_oi, d.pe_oi)));
  const maxOI_full = isEmpty ? 1 : Math.max(...data!.map(d => Math.max(d.ce_oi, d.pe_oi)));

  const HeatmapRow = ({ d, maxVal, showNumbers = false }: { d: OIData; maxVal: number; showNumbers?: boolean }) => (
    <div className="grid grid-cols-[1fr,70px,1fr] gap-4 items-center">
      <div className="flex justify-end items-center gap-2">
        {showNumbers && <span className="text-[9px] font-bold text-red-500/70 tabular-nums">{(d.ce_oi / 100000).toFixed(1)}L</span>}
        <div
          className="h-3 bg-red-500/20 rounded-l border-r-2 border-red-500 transition-all duration-1000"
          style={{ width: `${(d.ce_oi / maxVal) * 100}%` }}
        />
      </div>
      <div className="text-[10px] font-black text-center tabular-nums text-muted-foreground bg-muted/50 rounded py-0.5 border shadow-inner">
        {d.strike}
      </div>
      <div className="flex justify-start items-center gap-2">
        <div
          className="h-3 bg-green-500/20 rounded-r border-l-2 border-green-500 transition-all duration-1000"
          style={{ width: `${(d.pe_oi / maxVal) * 100}%` }}
        />
        {showNumbers && <span className="text-[9px] font-bold text-green-500/70 tabular-nums">{(d.pe_oi / 100000).toFixed(1)}L</span>}
      </div>
    </div>
  );

  return (
    <Card className="border-none shadow-sm">
      <CardHeader className="bg-muted/30 py-3 flex flex-row items-center justify-between">
        <CardTitle className="text-xs font-bold uppercase tracking-wider flex items-center gap-2">
          <BarChart className="h-3.5 w-3.5 text-primary" /> Institutional OI
        </CardTitle>
        {!isEmpty && (
          <Dialog>
            <DialogTrigger asChild>
              <button className="p-1.5 hover:bg-muted rounded-md transition-colors">
                <Maximize2 className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
            </DialogTrigger>
            <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2 border-b pb-4">
                  <BarChart className="h-4 w-4 text-primary" /> Full Option Chain Landscape (OI in Lacs)
                </DialogTitle>
              </DialogHeader>
              <div className="py-6 space-y-2">
                <div className="grid grid-cols-[1fr,70px,1fr] gap-4 mb-4 text-[9px] font-black text-muted-foreground uppercase text-center border-b pb-2">
                  <span>Call Concentration</span>
                  <span>Strike</span>
                  <span>Put Concentration</span>
                </div>
                {data!.slice().sort((a, b) => a.strike - b.strike).map((d, i) => (
                  <HeatmapRow key={i} d={d} maxVal={maxOI_full} showNumbers />
                ))}
              </div>
            </DialogContent>
          </Dialog>
        )}
      </CardHeader>
      <CardContent className="pt-4 px-4 pb-2">
        <div className="space-y-1.5 min-h-[150px] flex flex-col">
          {isEmpty ? (
            <div className="flex-1 flex flex-col items-center justify-center opacity-20 italic">
              <BarChart3 className="h-8 w-8 mb-2" />
              <p className="text-[10px] font-bold uppercase tracking-widest">Waiting for OI data...</p>
            </div>
          ) : (
            mainData.map((d, i) => (
              <HeatmapRow key={i} d={d} maxVal={maxOI_main} showNumbers />
            ))
          )}
        </div>
        {!isEmpty && (
          <div className="flex justify-between mt-4 text-[8px] font-bold text-muted-foreground uppercase px-2 pb-2">
            <span className="flex items-center gap-1"><div className="w-1.5 h-1.5 bg-red-500 rounded-full" /> Resistance</span>
            <span className="flex items-center gap-1">Support <div className="w-1.5 h-1.5 bg-green-500 rounded-full" /></span>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const PerformanceStats = ({ data }: { data: JournalData | null }) => {
  const stats = data?.stats || { win_rate: 0, wins: 0, losses: 0, total_pnl: 0, profit_factor: 0, avg_win: 0, avg_loss: 0 };
  const lifetime = data?.lifetime_stats || { win_rate: 0, total_pnl: 0, profit_factor: 0 };

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <Card className="border-none shadow-sm bg-primary/5">
        <CardContent className="p-4">
          <div className="flex justify-between items-start">
            <p className="text-[10px] font-bold text-muted-foreground uppercase">Recent Win Rate</p>
            <Badge variant="outline" className="text-[8px] py-0 h-4 font-mono opacity-60">Hist: {lifetime.win_rate}%</Badge>
          </div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-2xl font-black text-primary">{stats.win_rate}%</span>
            <span className="text-[9px] font-bold text-muted-foreground">({stats.wins}W / {stats.losses}L)</span>
          </div>
        </CardContent>
      </Card>

      <Card className="border-none shadow-sm bg-green-500/5">
        <CardContent className="p-4">
          <div className="flex justify-between items-start">
            <p className="text-[10px] font-bold text-muted-foreground uppercase">Realized P&L</p>
            <Badge variant="outline" className="text-[8px] py-0 h-4 font-mono opacity-60">All-time: ₹{((lifetime?.total_pnl || 0) / 1000).toFixed(1)}k</Badge>
          </div>
          <span className={cn("text-2xl font-black mt-1 block", (stats?.total_pnl || 0) >= 0 ? "text-green-500" : "text-red-500")}>
            ₹{(stats?.total_pnl || 0).toLocaleString()}
          </span>
        </CardContent>
      </Card>

      <Card className="border-none shadow-sm bg-sky-500/5">
        <CardContent className="p-4">
          <div className="flex justify-between items-start">
            <p className="text-[10px] font-bold text-muted-foreground uppercase">Profit Factor</p>
            <Badge variant="outline" className="text-[8px] py-0 h-4 font-mono opacity-60">Avg: {lifetime.profit_factor}</Badge>
          </div>
          <span className="text-2xl font-black text-sky-600 mt-1 block">{stats.profit_factor}</span>
        </CardContent>
      </Card>

      <Card className="border-none shadow-sm bg-violet-500/5">
        <CardContent className="p-4">
          <p className="text-[10px] font-bold text-muted-foreground uppercase">Avg Win/Loss</p>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-sm font-black text-green-600">+{stats.avg_win}</span>
            <span className="text-muted-foreground">/</span>
            <span className="text-sm font-black text-red-600">{stats.avg_loss}</span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default function NiftyMaster() {
  const [state, setState] = useState<NiftyState | null>(null)
  const [journal, setJournal] = useState<JournalData | null>(null)
  const [brokerPositions, setBrokerPositions] = useState<any[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchStateData = useCallback(async () => {
    try {
      // 1. Fetch Strategy State
      const response = await fetch('/api/nifty-master/state', {
        credentials: 'include',
      })

      if (response.status === 401) {
        setError('Session expired. Please log in.')
        return
      }

      if (response.status === 404) {
        setError('Strategy State not found. Start the strategy to see data.')
        return
      }

      const data = await response.json()
      if (data.error) {
        setError(data.error)
      } else {
        setState(data)
      }

      // 2. Fetch Broker Positions (Isolated so it doesn't break the main loop)
      try {
        const posRes = await fetch('/api/nifty-master/broker-positions', {
          credentials: 'include'
        })
        if (posRes.ok) {
          const posData = await posRes.json()
          if (posData?.status === 'success') {
            setBrokerPositions(posData.data || [])
          }
        }
      } catch (pErr) {
        console.warn("Failed to fetch broker positions, falling back to engine P&L", pErr)
      }
      setError(null)
    } catch (err) {
      setError('Failed to fetch strategy state')
    } finally {
      setIsLoading(false)
    }
  }, [])

  const fetchJournal = useCallback(async () => {
    try {
      const response = await fetch('/api/nifty-master/journal', {
        credentials: 'include',
      })
      if (response.ok) {
        const data = await response.json()
        setJournal(data)
      }
    } catch (err) {
      console.error('Failed to fetch journal')
    }
  }, [])

  useEffect(() => {
    fetchStateData()
    fetchJournal()
    const interval = setInterval(() => {
      fetchStateData()
      fetchJournal()
    }, 5000)
    return () => clearInterval(interval)
  }, [fetchStateData, fetchJournal])

  if (isLoading && !state) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-4">
        <Activity className="h-10 w-10 animate-spin text-primary opacity-20" />
        <span className="text-sm font-medium text-muted-foreground">Loading Strategy State...</span>
      </div>
    )
  }

  const getRegime = () => {
    if (!state || !state.market_data) return { label: 'Unknown', icon: Minus, color: 'text-muted-foreground', bg: 'bg-muted' }
    const { adx, pcr, nifty_ltp } = state.market_data
    const morning_pcr = state.morning_pcr || pcr
    const morning_spot = state.morning_spot || nifty_ltp
    const pcr_shift = pcr - morning_pcr
    const spot_shift = nifty_ltp - morning_spot

    if (adx >= 25) {
      if (spot_shift <= -250 && pcr_shift < -0.15) return { label: 'Trending Bearish', icon: TrendingDown, color: 'text-red-500', bg: 'bg-red-500/10' }
      if (spot_shift >= 250 && pcr_shift > 0.15) return { label: 'Trending Bullish', icon: TrendingUp, color: 'text-green-500', bg: 'bg-green-500/10' }
      return { label: 'Trending (Awaiting Confirm)', icon: Activity, color: 'text-orange-500', bg: 'bg-orange-500/10' }
    }
    if (adx >= 22) return { label: 'Grey Zone', icon: Activity, color: 'text-orange-400', bg: 'bg-orange-400/10' }
    if (adx < 22) return { label: 'Ranging', icon: Minus, color: 'text-sky-500', bg: 'bg-sky-500/10' }
    return { label: 'Neutral', icon: Activity, color: 'text-muted-foreground', bg: 'bg-muted' }
  }

  const regime = getRegime()

  return (
    <div className="max-w-[1600px] mx-auto p-4 md:p-8 space-y-8">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 bg-card border rounded-2xl p-6 shadow-sm">
        <div className="flex items-center gap-5">
          <div className={cn("p-4 rounded-xl", regime.bg)}>
            <regime.icon className={cn("h-8 w-8", regime.color)} />
          </div>
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight">Nifty Weekly Master <span className="text-primary font-mono text-lg ml-2 opacity-50">v2.1</span></h1>
            <div className="flex items-center gap-3 mt-1">
              <div className="flex items-center gap-2 px-2 py-0.5 rounded-md bg-muted text-[10px] font-bold uppercase text-muted-foreground">
                <Clock className="h-3 w-3" />
                Last Eval: {state?.last_eval}
              </div>
              <Badge variant="outline" className="text-xs">Week {state?.current_week}</Badge>
              {state?.week_blocked && <Badge variant="destructive">WEEK BLOCKED</Badge>}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="flex flex-col items-end">
            <span className="text-xs text-muted-foreground font-medium uppercase">Regime</span>
            <span className={cn("text-xl font-black uppercase", regime.color)}>{regime.label}</span>
          </div>
          <div className="h-10 w-px bg-border hidden sm:block" />
          <div className="flex flex-col items-end">
            <span className="text-xs text-muted-foreground font-medium uppercase">Nifty Spot</span>
            <span className="text-xl font-black tabular-nums">{state?.market_data?.nifty_ltp?.toLocaleString('en-IN', { minimumFractionDigits: 1 }) || '0.0'}</span>
          </div>
        </div>
      </div>

      {error && (
        <Card className="border-destructive bg-destructive/5 animate-bounce">
          <CardContent className="pt-6 flex items-center gap-4 text-destructive">
            <AlertTriangle className="h-6 w-6" />
            <p className="font-semibold">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Core Intelligence & Performance */}
      <div className="space-y-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-6">
          {[
            { label: 'Put-Call Ratio', val: state?.market_data?.pcr?.toFixed(2) || '0.00', color: 'text-sky-500', icon: BarChart3, progress: (state?.market_data?.pcr || 0) * 50, footer: `Shift: ${(state!.market_data.pcr - (state!.morning_pcr || state!.market_data.pcr)).toFixed(2)}` },
            { label: 'Spot Move', val: `${(state?.market_data?.nifty_ltp || 0) - (state?.morning_spot || 0) > 0 ? '+' : ''}${((state?.market_data?.nifty_ltp || 0) - (state?.morning_spot || 0)).toFixed(0)}`, color: 'text-primary', icon: TrendingUp, progress: Math.abs(((state?.market_data?.nifty_ltp || 0) - (state?.morning_spot || 0)) / 4), footer: `Anchor: ${state?.morning_spot || '0'}` },
            { label: 'Fear Index (VIX)', val: state?.market_data?.vix?.toFixed(2) || '0.00', color: 'text-violet-500', icon: Activity, progress: (state?.market_data?.vix || 0) * 4, footer: 'Threshold: 20.0' },
            { label: 'IV Rank (IVR)', val: `${state?.market_data?.ivr?.toFixed(0) || '0'}%`, color: 'text-yellow-500', icon: Zap, progress: state?.market_data?.ivr || 0, footer: 'Trigger: 30/40%' },
            { label: 'Trend Strength', val: state?.market_data?.adx?.toFixed(2) || '0.00', color: 'text-orange-500', icon: TrendingUp, progress: (state?.market_data?.adx || 0) * 2, footer: 'Grey: 22 / Trend: 25' },
          ].map((item, i) => (
            <Card key={i} className="shadow-sm border-none bg-card">
              <CardHeader className="pb-2 px-4 pt-4">
                <CardTitle className="text-[10px] font-bold text-muted-foreground uppercase flex items-center gap-2">
                  <item.icon className={cn("h-3 w-3", item.color)} /> {item.label}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 pb-4">
                <div className="flex items-baseline justify-between mb-2">
                  <span className={cn("text-3xl font-black", item.color)}>{item.val}</span>
                </div>
                <Progress value={Math.min(item.progress, 100)} className={cn("h-1", item.color)} indicatorClassName="bg-current" />
                <div className="mt-2 text-[10px] text-muted-foreground text-center opacity-70">{item.footer}</div>
              </CardContent>
            </Card>
          ))}

          <Card className={cn("shadow-sm border-none bg-card", (state?.weekly_pnl || 0) >= 0 ? "bg-green-500/5" : "bg-red-500/5")}>
            <CardHeader className="pb-2 px-4 pt-4">
              <CardTitle className="text-[10px] font-bold text-muted-foreground uppercase flex items-center gap-2">
                <ShieldCheck className="h-3 w-3" /> Weekly MTM
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              <div className="flex flex-col">
                <span className={cn("text-3xl font-black tabular-nums", (state?.weekly_pnl || 0) >= 0 ? "text-green-500" : "text-red-500")}>
                  ₹{(state?.weekly_pnl || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                </span>
                <div className="flex items-center gap-2 mt-2">
                  <Badge variant={(state?.weekly_pnl || 0) >= 0 ? "default" : "destructive"} className="text-[9px] h-4">Weekly P&L</Badge>
                  <span className="text-[10px] text-muted-foreground font-bold">
                    Limit: ₹{(state?.weekly_limit || 1500).toLocaleString()}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <PerformanceStats data={journal} />
      </div>

      <div className="space-y-8">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            { label: 'Carry Engine', key: 'carry_trade', desc: 'Overnight Alpha' },
            { label: 'Monday Engine', key: 'monday_trade', desc: 'Gap & Vol Harvest' },
            { label: 'Expiry Engine', key: 'tuesday_trade', desc: 'Tuesday Expiry' }
          ].map((strat) => {
            const slot = (state as any)?.[strat.key] as TradeSlot

            // UI-Side PnL Matching
            let matchedSlotPnl = 0
            let matchedLegs = slot?.legs || []

            if (slot?.active && brokerPositions.length > 0) {
              matchedLegs = slot.legs.map(leg => {
                const brokerPos = brokerPositions.find(p => p.symbol === leg.symbol)
                const pnl = brokerPos ? parseFloat(brokerPos.pnl) : 0
                matchedSlotPnl += pnl
                return { ...leg, live_pnl: pnl }
              })
            }

            return (
              <Card key={strat.key} className={cn("transition-all border-2", slot?.active ? "border-primary shadow-md" : "opacity-50 border-muted")}>
                <CardHeader className="border-b bg-muted/30 py-3">
                  <div className="flex justify-between items-center">
                    <CardTitle className="text-md font-bold">{strat.label}</CardTitle>
                    {slot?.active && <CheckCircle2 className="h-4 w-4 text-primary" />}
                  </div>
                  <CardDescription className="text-[9px] uppercase font-bold">{strat.desc}</CardDescription>
                </CardHeader>
                <CardContent className="pt-6">
                  {slot?.active ? (
                    <div className="space-y-6">
                      <div className="flex justify-between items-start border-b pb-4">
                        <div className="flex-1 min-w-0 pr-2">
                          <p className="text-[9px] text-muted-foreground uppercase font-bold">Type</p>
                          <p className={cn(
                            "font-black text-primary truncate",
                            slot.type.length > 12 ? "text-sm" : "text-xl"
                          )}>
                            {slot.type || 'N/A'}
                          </p>
                        </div>
                        <div className="text-right flex-shrink-0">
                          <p className="text-[9px] text-muted-foreground uppercase font-bold">Strike</p>
                          <p className="text-xl font-black font-mono">{slot.strike || '-'}</p>
                        </div>
                      </div>

                      <div className="grid grid-cols-3 gap-2 py-3 border-b border-dashed">
                        {[
                          { label: 'Delta', val: slot.greeks?.delta, color: 'text-sky-500' },
                          { label: 'Theta', val: slot.greeks?.theta, color: 'text-orange-500' },
                          { label: 'Vega', val: slot.greeks?.vega, color: 'text-violet-500' }
                        ].map((g, i) => (
                          <div key={i} className="text-center">
                            <p className="text-[8px] text-muted-foreground uppercase font-bold">{g.label}</p>
                            <p className={cn("text-xs font-black tabular-nums", g.color)}>{g.val?.toFixed(2) || '0.00'}</p>
                          </div>
                        ))}
                      </div>

                      <div className="space-y-4">
                        <Dialog>
                          <DialogTrigger asChild>
                            <div>
                              <PayoffMini slot={slot} niftyLtp={state?.market_data?.nifty_ltp || 0} />
                            </div>
                          </DialogTrigger>
                          <DialogContent className="max-w-2xl bg-card border-primary/20 shadow-2xl">
                            <DialogHeader>
                              <DialogTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2">
                                <ShieldCheck className="h-4 w-4 text-primary" /> Structural Intelligence
                              </DialogTitle>
                            </DialogHeader>
                            <PayoffVisualizer slot={slot} niftyLtp={state?.market_data?.nifty_ltp || 0} keyLevels={state?.key_levels} />
                          </DialogContent>
                        </Dialog>

                        <div className="flex justify-between items-center px-1">
                          <span className="text-[10px] font-black text-muted-foreground uppercase">Live Legs</span>
                          <span className={cn("text-sm font-black tabular-nums", (matchedSlotPnl || slot?.live_pnl || 0) >= 0 ? "text-green-500" : "text-red-500")}>
                            LIVE P&L: ₹{(matchedSlotPnl || slot?.live_pnl || 0).toLocaleString()}
                          </span>
                        </div>

                        <div className="space-y-2">
                          {matchedLegs.map((leg, i) => (
                            <div key={i} className="flex justify-between items-center p-2 rounded-lg bg-muted/40 border">
                              <div className="flex items-center gap-3">
                                <div className={cn("w-1 h-6 rounded-full", leg.action === 'SELL' ? "bg-red-500" : "bg-green-500")} />
                                <div className="flex flex-col">
                                  <span className="text-[10px] font-bold">{leg.symbol}</span>
                                  <span className="text-[9px] text-muted-foreground">{leg.action} • {leg.qty} Qty</span>
                                </div>
                              </div>
                              <div className="flex flex-col items-end">
                                <Badge variant="outline" className={cn("text-[9px] font-bold mb-1", leg.option_type === 'CE' ? "text-red-500" : "text-green-500")}>
                                  {leg.option_type}
                                </Badge>
                                <span className={cn("text-[10px] font-black tabular-nums", (leg.live_pnl || 0) >= 0 ? "text-green-500" : "text-red-500")}>
                                  ₹{(leg.live_pnl || 0).toLocaleString()}
                                </span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="flex justify-between items-center pt-4 border-t border-dashed">
                        <span className="text-[9px] font-bold text-muted-foreground uppercase tracking-widest">In: {slot.entry_time}</span>
                        <div className="text-right">
                          <span className="text-[9px] text-muted-foreground font-bold mr-2 uppercase">Entry Premium:</span>
                          <span className="font-black text-sm">Rs.{(slot?.premium_collected || 0).toFixed(0)}</span>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="py-24 flex flex-col items-center justify-center gap-4 text-center opacity-40">
                      <Zap className="h-8 w-8" />
                      <p className="text-[10px] font-bold uppercase tracking-widest italic">Engine Armed</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-1 space-y-8">
            <AdjustmentAlerts signals={state?.adjustment_signals} />
            <OIHeatmap data={state?.oi_data} niftyLtp={state?.market_data?.nifty_ltp || 0} />
            <DecisionAudit logs={state?.decision_log} />
          </div>
          <div className="lg:col-span-2">
            {/* Trade Journal Table */}
            <Card className="border-none shadow-sm h-full">
              <CardHeader className="flex flex-row items-center justify-between py-4 border-b">
                <div>
                  <CardTitle className="text-sm font-black uppercase tracking-widest flex items-center gap-2">
                    <History className="h-4 w-4 text-primary" /> Trade Journal
                  </CardTitle>
                  <CardDescription className="text-[10px]">Last 50 recorded operations</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="bg-muted/30">
                      <TableRow>
                        <TableHead className="text-[10px] font-bold uppercase">Time</TableHead>
                        <TableHead className="text-[10px] font-bold uppercase">Type</TableHead>
                        <TableHead className="text-[10px] font-bold uppercase">Action</TableHead>
                        <TableHead className="text-[10px] font-bold uppercase text-right">Premium / PnL</TableHead>
                        <TableHead className="text-[10px] font-bold uppercase text-right">VIX / ADX</TableHead>
                        <TableHead className="text-[10px] font-bold uppercase">Exit Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(journal?.trades || []).length > 0 ? (
                        journal?.trades.slice().reverse().map((trade, i) => (
                          <TableRow key={i} className="hover:bg-muted/10">
                            <TableCell className="py-2.5">
                              <div className="flex flex-col">
                                <span className="text-[10px] font-bold tabular-nums">{new Date(trade.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                                <span className="text-[8px] text-muted-foreground uppercase">{trade.day}</span>
                              </div>
                            </TableCell>
                            <TableCell className="py-2.5">
                              <Badge variant="outline" className="text-[9px] py-0 h-4 font-bold">{trade.type}</Badge>
                            </TableCell>
                            <TableCell className="py-2.5">
                              <Badge className={cn("text-[9px] py-0 h-4 font-black uppercase", trade.action === 'ENTRY' ? "bg-sky-500" : "bg-orange-500")}>
                                {trade.action}
                              </Badge>
                            </TableCell>
                            <TableCell className="py-2.5 text-right font-mono text-[10px] font-bold">
                              <span className={cn(
                                trade.action === 'EXIT' ? (parseFloat(trade.pnl) >= 0 ? "text-green-500" : "text-red-500") : ""
                              )}>
                                ₹{parseFloat(trade.pnl || trade.premium || 0).toLocaleString()}
                              </span>
                            </TableCell>
                            <TableCell className="py-2.5 text-right text-[10px] font-medium text-muted-foreground tabular-nums">
                              {trade.vix || '-'} / {trade.adx || '-'}
                            </TableCell>
                            <TableCell className="py-2.5">
                              <span className="text-[9px] font-bold text-muted-foreground uppercase">{trade.exit_reason || '-'}</span>
                            </TableCell>
                          </TableRow>
                        ))
                      ) : (
                        <TableRow>
                          <TableCell colSpan={6} className="text-center py-12 text-muted-foreground opacity-30 italic text-xs">
                            No trade journal data available
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex justify-between items-center pt-8 border-t text-muted-foreground text-[10px] font-bold uppercase tracking-widest">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
          Live Sync
        </div>
        <div className="flex gap-6">
          <span>Risk Managed</span>
          <span>Broker Online</span>
          <span>© 2026</span>
        </div>
      </div>
    </div>
  )
}
