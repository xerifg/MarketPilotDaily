import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { horizonLabels, currencyLabels, symbol as symbolSchema, type Currency, type Instrument, type Portfolio, type Position, type Profile } from '../shared/schema';
import { api, ApiError } from './api';
import { Reports } from './Reports';

type Tab = 'positions' | 'daily' | 'settings';
const currencies: Currency[] = ['CNY', 'USD'];
const fmtDate = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
}).format(new Date(value));
const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试。';

function HorizonOptions() {
  return <><option value="">尚未确定</option>{Object.entries(horizonLabels).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</>;
}

function Dialog({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} onCancel={event => { event.preventDefault(); close(); }}>
    <div className="dialog-header"><h2>{title}</h2><button className="icon-button" aria-label="关闭对话框" onClick={close}>×</button></div>
    {children}
  </dialog>;
}

function PositionForm({ position, portfolio, onSaved, close }: {
  position?: Position; portfolio: Portfolio; onSaved: (value: Portfolio) => void; close: () => void;
}) {
  const [exchange, setExchange] = useState(position?.exchange ?? 'SH');
  const [code, setCode] = useState(position?.code ?? '');
  const [instrument, setInstrument] = useState<Instrument | null>(position ?? null);
  const [quantity, setQuantity] = useState(position?.quantity ?? '');
  const [cost, setCost] = useState(position?.averageCost ?? '');
  const [horizon, setHorizon] = useState(position?.horizon ?? '');
  const [thesis, setThesis] = useState(position?.thesis ?? '');
  const [revision, setRevision] = useState(portfolio.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<Portfolio | null>(null);
  const symbol = `${code}.${exchange}`;
  const validInstrument = instrument?.symbol === symbol;
  const isUS = exchange === 'US';

  async function lookup() {
    setBusy(true); setError(''); setInstrument(null);
    try { setInstrument(await api<Instrument>(`/api/instruments?symbol=${encodeURIComponent(symbol)}`)); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const body = { revision, quantity, averageCost: cost.trim() || null, horizon: horizon || null, thesis };
      const result = await api<Portfolio>(position ? `/api/positions/${position.id}` : '/api/positions', position ? 'PATCH' : 'POST',
        position ? body : { ...body, symbol });
      onSaved(result); close();
    } catch (error) { setError(errorText(error)); setConflict(error instanceof ApiError && error.status === 409); }
    finally { setBusy(false); }
  }
  async function loadLatest() {
    setBusy(true);
    try { setLatest(await api<Portfolio>('/api/portfolio')); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  const current = latest?.positions.find(item => position ? item.id === position.id : item.symbol === symbol);
  const canRebase = latest && (position ? Boolean(current) : !current);

  return <Dialog title={position ? '编辑持仓' : '添加一笔持仓'} close={() => { if (!busy) close(); }}>
    <form onSubmit={submit}>
      <p className="muted">记录当前持有数量和平均成本。买卖之后，在这里更新。</p>
      <fieldset disabled={busy}>
        {!position && <div className="field-row">
          <label>市场／交易所<select value={exchange} onChange={event => { setExchange(event.target.value as Instrument['exchange']); setInstrument(null); }}>
            <option value="SH">A 股 · 上海 SH</option><option value="SZ">A 股 · 深圳 SZ</option><option value="BJ">A 股 · 北京 BJ</option><option value="US">美股 US</option>
          </select></label>
          <label>证券代码<input autoFocus value={code} onChange={event => { setCode(event.target.value.trim().toUpperCase()); setInstrument(null); }} placeholder={isUS ? '例如 AAPL、VOO、BRK.B' : '例如 510300'} maxLength={isUS ? 29 : 6} required inputMode={isUS ? 'text' : 'numeric'} autoCapitalize="characters" spellCheck={false} /></label>
          <button type="button" className="secondary lookup-button" onClick={lookup} disabled={!symbolSchema.safeParse(symbol).success}>核验证券</button>
        </div>}
        {validInstrument && <div className="verified"><span className="status-dot" /><strong>{instrument.name}</strong><span>{instrument.symbol} · {instrument.assetType === 'etf' ? 'ETF' : '股票'} · {currencyLabels[instrument.currency]}</span></div>}
        <div className="field-row">
          <label>持有数量<input value={quantity} onChange={event => setQuantity(event.target.value)} placeholder="例如 100" inputMode="decimal" required /></label>
          <label>平均成本（{isUS ? '美元' : '人民币'}／份或股）<input value={cost} onChange={event => setCost(event.target.value)} placeholder="未知可留空" inputMode="decimal" /></label>
        </div>
        <label>计划持有期限<select value={horizon} onChange={event => setHorizon(event.target.value as typeof horizon)}><HorizonOptions /></select></label>
        <label>买入理由 <span className="optional">选填</span><textarea value={thesis} onChange={event => setThesis(event.target.value)} placeholder="为什么持有？什么变化会让你重新考虑？" rows={3} maxLength={1000} /></label>
      </fieldset>
      {error && <div className="error" role="alert">{error}</div>}
      {conflict && <div className="conflict">
        <p>你的输入仍然保留。先读取最新记录，核对后再决定是否保存。</p>
        <button type="button" className="secondary" disabled={busy} onClick={loadLatest}>读取最新记录</button>
        {latest && <><p>最新版本 v{latest.revision}：{current ? `${current.name}，数量 ${current.quantity}，成本 ${current.averageCost ?? '未知'} ${current.currency}` : '当前没有这笔持仓'}。</p>
          {current && <p>持有期限：{current.horizon ? horizonLabels[current.horizon] : '尚未确定'}。买入理由：{current.thesis || '未填写'}。</p>}
          {canRebase ? <button type="button" className="secondary" onClick={() => { setRevision(latest.revision); setConflict(false); setError('已采用最新版本号，请核对表单后重新保存。'); }}>已核对，保留我的输入</button>
            : <p>该记录已删除或同一证券已存在。请关闭表单、刷新列表后操作。</p>}</>}
      </div>}
      <div className="dialog-actions"><button type="button" className="secondary" onClick={close} disabled={busy}>取消</button>
        <button type="submit" className="primary" disabled={busy || !validInstrument || conflict}>{busy ? '处理中…' : '保存持仓'}</button></div>
    </form>
  </Dialog>;
}

function Settings({ portfolio, onSaved }: { portfolio: Portfolio; onSaved: (value: Portfolio) => void }) {
  const [profile, setProfile] = useState<Profile>(portfolio.profile);
  const [cash, setCash] = useState({ CNY: portfolio.cash.CNY ?? '', USD: portfolio.cash.USD ?? '' });
  const [revision, setRevision] = useState(portfolio.revision);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  async function save(kind: Currency | 'profile', event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const result = await api<Portfolio>(`/api/${kind === 'profile' ? 'profile' : 'cash'}`, 'PATCH', {
        revision, ...(kind === 'profile' ? profile : { currency: kind, cash: cash[kind].trim() || null }),
      });
      setRevision(result.revision); onSaved(result);
      setMessage(kind === 'profile' ? '投资偏好已保存。' : `${currencyLabels[kind]}现金已保存。`);
    } catch (error) { setError(errorText(error)); setConflict(error instanceof ApiError && error.status === 409); }
    finally { setBusy(false); }
  }
  async function reset() {
    setBusy(true);
    try {
      const result = await api<Portfolio>('/api/portfolio');
      setProfile(result.profile); setCash({ CNY: result.cash.CNY ?? '', USD: result.cash.USD ?? '' }); setRevision(result.revision); onSaved(result);
      setConflict(false); setError(''); setMessage('已加载最新设置，请重新编辑。');
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <div className="settings-grid">
    <section className="panel"><div className="section-heading"><span className="eyebrow">AVAILABLE CASH</span><h2>可用现金</h2><p>分币种维护余额，修改持仓不会自动扣减现金。</p></div>
      {currencies.map(currency => <form className="cash-form" key={currency} onSubmit={event => save(currency, event)}>
        <label>{currencyLabels[currency]}（{currency}）<input value={cash[currency]} onChange={event => setCash({ ...cash, [currency]: event.target.value })} inputMode="decimal" placeholder="未知留空；没有现金填 0" disabled={busy} /></label>
        <p className="muted">{portfolio.cashAsOf[currency] ? `上次记录：${fmtDate(portfolio.cashAsOf[currency])}` : '尚未记录现金'}</p>
        <button className="primary" disabled={busy || conflict}>保存{currencyLabels[currency]}现金</button>
      </form>)}
      <p className="muted">汇率尚未接入，各币种余额不会直接相加。</p>
    </section>
    <section className="panel"><div className="section-heading"><span className="eyebrow">YOUR BOUNDARIES</span><h2>投资偏好</h2><p>不确定的项目可以留空，AI 不会替你设定风险承受能力。</p></div>
      <form onSubmit={event => save('profile', event)}><fieldset disabled={busy}>
        <label>主要投资期限<select value={profile.horizon ?? ''} onChange={event => setProfile({ ...profile, horizon: (event.target.value || null) as Profile['horizon'] })}><HorizonOptions /></select></label>
        <div className="field-row"><label>可承受回撤（%）<input value={profile.maxDrawdown ?? ''} onChange={event => setProfile({ ...profile, maxDrawdown: event.target.value || null })} inputMode="decimal" placeholder="尚未确定" /></label>
          <label>单只持仓上限（%）<input value={profile.maxPosition ?? ''} onChange={event => setProfile({ ...profile, maxPosition: event.target.value || null })} inputMode="decimal" placeholder="尚未确定" /></label></div>
        <label className="checkbox"><input type="checkbox" checked={profile.emailPaused} onChange={event => setProfile({ ...profile, emailPaused: event.target.checked })} />暂停每日邮件</label>
        <p className="muted">保存后生效：每日任务在领取报告和提交邮件之前，都会检查这个开关。</p>
      </fieldset><button className="primary" disabled={busy || conflict}>保存偏好</button></form>
    </section>
    <div className="settings-feedback">{error && <p className="error" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
      {conflict && <button className="secondary" disabled={busy} onClick={reset}>放弃本页未保存修改，加载最新设置</button>}</div>
  </div>;
}

export default function App() {
  const [tab, setTab] = useState<Tab>('positions');
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [editing, setEditing] = useState<Position | 'new' | null>(null);
  const [deleting, setDeleting] = useState<Position | null>(null);
  const [undo, setUndo] = useState<{ id: string; name: string; revision: number; expires: number } | null>(null);
  const [search, setSearch] = useState('');
  const [settingsKey, setSettingsKey] = useState(0);
  async function logout() {
    setMutating(true); setError('');
    try { await api('/auth/logout', 'POST', {}); window.location.replace('/'); }
    catch (error) { setError(errorText(error)); setMutating(false); }
  }
  async function refresh() {
    setLoading(true); setError('');
    try { setPortfolio(await api<Portfolio>('/api/portfolio')); setSettingsKey(value => value + 1); }
    catch (error) { setError(errorText(error)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => setUndo(null), Math.max(0, undo.expires - Date.now()));
    return () => clearTimeout(timer);
  }, [undo]);
  function saved(value: Portfolio) {
    setPortfolio(value); setError('');
    setNotice(value.storage === 'local' ? '已保存到本地数据库。' : '已同步到云端。');
  }
  async function remove() {
    if (!deleting || !portfolio) return;
    setMutating(true); setError('');
    const expires = Date.now() + 30000;
    try {
      const value = await api<Portfolio>(`/api/positions/${deleting.id}`, 'DELETE', { revision: portfolio.revision });
      saved(value); setUndo({ id: deleting.id, name: deleting.name, revision: value.revision, expires }); setDeleting(null);
    } catch (error) { setError(errorText(error)); setDeleting(null); }
    finally { setMutating(false); }
  }
  async function restore() {
    if (!undo) return;
    setMutating(true);
    try { saved(await api<Portfolio>(`/api/positions/${undo.id}/restore`, 'POST', { revision: undo.revision })); setUndo(null); }
    catch (error) { setError(errorText(error)); }
    finally { setMutating(false); }
  }
  const positions = portfolio?.positions.filter(item => `${item.name} ${item.symbol}`.toLowerCase().includes(search.toLowerCase())) ?? [];
  const unknownCosts = portfolio?.positions.filter(item => item.averageCost === null).length ?? 0;

  return <div className="app-shell">
    <aside className="sidebar"><a href="/" className="brand"><span className="brand-icon">M<span>↗</span></span><span>MarketPilot<small>DAILY · 每日投资笔记</small></span></a>
      <div className="nav-caption">我的工作台</div><nav aria-label="主导航">
        <button className={tab === 'positions' ? 'nav-item active' : 'nav-item'} onClick={() => setTab('positions')}><span aria-hidden="true">▦</span>我的持仓</button>
        <button className={tab === 'daily' ? 'nav-item active' : 'nav-item'} onClick={() => setTab('daily')}><span aria-hidden="true">▤</span>分析日报</button>
        <button className={tab === 'settings' ? 'nav-item active' : 'nav-item'} onClick={() => setTab('settings')}><span aria-hidden="true">⚙</span>投资设置</button>
      </nav><div className="sidebar-bottom"><span className="status-dot" />个人投资空间<p>先了解变化，再作决定。</p>{portfolio?.storage === 'cloud' && <button className="text-button" disabled={mutating} onClick={logout}>退出登录</button>}</div>
    </aside>
    <div className="main-shell"><header className="topbar"><span>个人工作台 <span className="slash">/</span> {{ positions: '持仓管理', daily: '分析日报', settings: '投资设置' }[tab]}</span>
      <span className="storage-pill">{portfolio ? portfolio.storage === 'local' ? '本地开发 · 本机数据' : '个人云端空间' : '正在连接'}</span></header>
      <main><div className="page-heading"><div><span className="eyebrow">{tab === 'positions' ? 'YOUR PORTFOLIO' : tab === 'daily' ? 'DAILY PERSPECTIVE' : 'INVESTMENT PROFILE'}</span>
        <h1>{{ positions: '让每一笔持仓，有据可依。', daily: '每天早上，读懂与你有关的变化。', settings: '先明确自己的投资边界。' }[tab]}</h1>
        <p>{{ positions: '维护你的真实持仓，让下一份日报更贴近你的投资计划。', daily: '以北京时间 05:15 为目标，通过 163 邮箱接收分析。', settings: '记录可用现金、持有期限和风险偏好，为后续分析提供依据。' }[tab]}</p></div>
        {tab === 'positions' && <button className="primary" disabled={!portfolio || loading} onClick={() => setEditing('new')}><span aria-hidden="true">＋</span> 添加持仓</button>}</div>
        {error && <div className="error" role="alert">{error} <button className="text-button" onClick={refresh} disabled={loading}>重新加载</button></div>}
        {notice && <div className="notice" role="status">{notice}<button className="text-button" onClick={() => setNotice('')} aria-label="关闭保存提示">×</button></div>}
        {portfolio?.storage === 'local' && <p className="local-note">开发预览：数据仅保存在这台电脑，尚未部署云端，也未启用 AI 与邮件发送。</p>}
        {loading && !portfolio && <div className="panel empty" role="status">正在读取持仓…</div>}
        {!loading && !portfolio && <div className="panel empty"><h2>暂时无法连接你的持仓</h2><p>恢复连接或完成登录后，点击“重新加载”。</p><a href="/auth/login" target="_blank" rel="noopener noreferrer">在新页面重新登录</a></div>}
        {portfolio && tab === 'positions' && <>
          <div className="metrics"><section className="metric"><span>当前持仓</span><strong>{portfolio.positions.length}<small>只证券</small></strong><p>A 股 · 美股 · ETF</p></section>
            <section className="metric"><span>可用现金 · 分币种</span><div className="cash-summary">{currencies.map(currency => <div key={currency}><span>{currency}</span><b>{portfolio.cash[currency] ?? '尚未填写'}</b></div>)}</div><button className="text-button" onClick={() => setTab('settings')}>管理现金 →</button></section>
            <section className="metric"><span>记录完整度</span><strong>{unknownCosts ? `${unknownCosts} 只` : portfolio.positions.length ? '成本已填写' : '等待添加'}</strong><p>{unknownCosts ? '平均成本未知，暂不计算对应盈亏' : '市值与盈亏将在行情接通后提供'}</p></section></div>
          <section className="panel positions-panel"><div className="list-toolbar"><div><h2>我的持仓 <span className="count">{portfolio.positions.length}</span></h2><p>版本 v{portfolio.revision} · 最近更新 {fmtDate(portfolio.updatedAt)}</p></div>
            <div className="list-controls"><input aria-label="搜索持仓" placeholder="搜索名称或代码" value={search} onChange={event => setSearch(event.target.value)} /><button className="secondary" onClick={refresh} disabled={loading}>{loading ? '读取中…' : '刷新'}</button></div></div>
            {!portfolio.positions.length ? <div className="empty"><div className="empty-symbol">＋</div><h3>从你的第一笔持仓开始</h3><p>添加证券、持有数量与成本。未知的成本可以留空。</p><button className="primary" onClick={() => setEditing('new')}>添加持仓</button></div>
              : !positions.length ? <div className="empty"><h3>没有匹配的持仓</h3><p>试试证券名称或完整代码。</p></div>
                : <div className="table-wrap"><table><thead><tr><th>证券</th><th>持有数量</th><th>平均成本 / 原币</th><th>持有期限</th><th>操作</th></tr></thead><tbody>{positions.map(item => <tr key={item.id}>
                  <td><div className="security-name">{item.name}<span className="tag">{item.assetType === 'etf' ? 'ETF' : '股票'}</span></div><div className="code">{item.symbol} · {currencyLabels[item.currency]}</div></td><td className="numeric">{item.quantity}</td><td className="numeric">{item.averageCost ?? <span className="muted">未知</span>} <span className="currency-code">{item.currency}</span></td><td>{item.horizon ? horizonLabels[item.horizon] : <span className="muted">尚未确定</span>}</td>
                  <td className="row-actions"><button className="text-button" aria-label={`编辑${item.name}`} onClick={() => setEditing(item)}>编辑</button><button className="text-button danger" aria-label={`删除${item.name}`} onClick={() => setDeleting(item)}>删除</button></td></tr>)}</tbody></table></div>}
            <div className="panel-footer">当前维护持仓快照；修改持仓不会自动生成日报或记录交易流水。</div></section>
          <div className="next-step"><span className="next-icon">↗</span><div><h3>持仓连接每日分析</h3><p>下一次日报使用更新后的持仓。查看已生成报告、数据覆盖和实际发送状态。</p></div><button className="text-button" onClick={() => setTab('daily')}>查看日报状态 →</button></div>
        </>}
        {portfolio && tab === 'daily' && <Reports paused={portfolio.profile.emailPaused} />}
        {portfolio && tab === 'settings' && <Settings key={settingsKey} portfolio={portfolio} onSaved={saved} />}
        <footer className="page-footer"><span>MarketPilotDaily</span><span>记录事实 · 识别风险 · 有条件地行动</span></footer>
      </main></div>
    {editing && portfolio && <PositionForm position={editing === 'new' ? undefined : editing} portfolio={portfolio} onSaved={saved} close={() => setEditing(null)} />}
    {deleting && <Dialog title="删除这笔持仓？" close={() => { if (!mutating) setDeleting(null); }}><p>将从当前持仓中移除「{deleting.name}」。历史报告中的记录不受影响，删除后 30 秒内可以撤销。</p><div className="dialog-actions"><button className="secondary" disabled={mutating} onClick={() => setDeleting(null)}>取消</button><button className="danger-button" disabled={mutating} onClick={remove}>{mutating ? '删除中…' : '确认删除'}</button></div></Dialog>}
    {undo && <div className="undo-toast" role="status">已删除 {undo.name}<button disabled={mutating} onClick={restore}>撤销删除</button><span>30 秒内有效</span></div>}
  </div>;
}
