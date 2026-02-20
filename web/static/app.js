const API = '/api';
const REFRESH_MS = 30000;
let modalEventId = null;

document.addEventListener('DOMContentLoaded', () => {
    initNav();
    initModal();
    initRefresh();
    loadAll();
    setInterval(loadStatus, REFRESH_MS);
});

// ── Navigation ──
function initNav() {
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });
}

// ── API ──
async function api(path, opts = {}) {
    const res = await fetch(API + path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    if (!res.ok) throw new Error(res.statusText);
    return res.json();
}

// ── Load All ──
async function loadAll() {
    await Promise.allSettled([loadStatus(), loadEEW(), loadRecordings(), loadConfig()]);
}

// ── Status ──
async function loadStatus() {
    try {
        const d = await api('/status');
        setDot(true);
        document.getElementById('recordingStatus').textContent = d.status === 'recording' ? '録画中' : '待機中';
        document.getElementById('totalFiles').textContent = d.total_files || 0;
        document.getElementById('protectedFiles').textContent = d.protected_files || 0;
        document.getElementById('channelName').textContent = d.channel?.name || '--';

        // 現在の録画
        const card = document.getElementById('currentRecordingCard');
        if (d.current_recording) {
            card.style.display = '';
            document.getElementById('currentRecordingBody').innerHTML =
                `<div class="kv"><span>ファイル</span><span>${esc(d.current_recording.file_path)}</span></div>
                 <div class="kv"><span>開始</span><span>${fmtDt(d.current_recording.start_time)}</span></div>`;
        } else {
            card.style.display = 'none';
        }

        // EEWステータスブロックはloadEEWで処理
    } catch {
        setDot(false);
    }
}

function setDot(ok) {
    const dot = document.querySelector('.dot');
    dot.className = ok ? 'dot ok' : 'dot err';
}

// ── EEW ──
async function loadEEW() {
    try {
        const d = await api('/eew_events');
        const events = d.events || [];
        renderEEWStatus(events);
        renderEEWTable(events);
    } catch { /* ignore */ }
}

function renderEEWStatus(events) {
    const block = document.getElementById('eewStatusBlock');
    const inner = document.getElementById('eewStatusInner');

    // 24時間以内のイベントを抽出
    const now = new Date();
    const cutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const recent = events.filter(e => new Date(e.occurrence_time) > cutoff);

    if (recent.length === 0) {
        block.className = 'eew-status quiet';
        inner.innerHTML = `<p class="eew-status-text">24時間以内にEEW（警報）は発表されていません。</p>`;
    } else {
        block.className = 'eew-status alert';
        let html = `<p class="eew-status-text">24時間以内にEEW（警報）が発表されています。</p><div class="eew-detail">`;
        for (const ev of recent) {
            const retLabel = ev.retention_hours >= 87600 ? '無期限' : ev.retention_hours + '時間';
            html += `
                <div class="eew-event-item">
                    <div class="eew-event-info">
                        <span class="eew-event-title">${fmtDt(ev.occurrence_time)}　${esc(ev.epicenter)}</span>
                        <span class="eew-event-meta">M${ev.magnitude !== null ? ev.magnitude.toFixed(1) : '-'} / 最大震度 ${esc(ev.max_intensity || '-')} / 保存 ${retLabel}</span>
                    </div>
                    <button class="btn btn-sm" onclick="openModal(${ev.id}, '${esc(ev.epicenter)}', ${ev.retention_hours})">延長</button>
                </div>`;
        }
        html += `</div>`;
        inner.innerHTML = html;
    }
}

function renderEEWTable(events) {
    const tbody = document.getElementById('eewTableBody');
    if (!events.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">EEWイベントはありません</td></tr>';
        return;
    }
    tbody.innerHTML = events.map(e => {
        const ret = e.retention_hours >= 87600 ? '無期限' : e.retention_hours + 'h';
        return `<tr>
            <td>${fmtDt(e.occurrence_time)}</td>
            <td>${esc(e.epicenter)}</td>
            <td>${e.magnitude !== null ? e.magnitude.toFixed(1) : '-'}</td>
            <td>${esc(e.max_intensity || '-')}</td>
            <td>${ret}</td>
        </tr>`;
    }).join('');
}

// ── Recordings ──
async function loadRecordings() {
    try {
        const d = await api('/recordings');
        const tbody = document.getElementById('recordingsTableBody');
        const recs = d.recordings || [];
        if (!recs.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="empty-cell">録画ファイルはありません</td></tr>';
            return;
        }
        tbody.innerHTML = recs.map(r => `<tr>
            <td>${fmtDt(r.start_time)}</td>
            <td>${fmtDt(r.end_time)}</td>
            <td title="${esc(r.file_path)}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.file_path)}</td>
            <td>${r.is_protected ? '<span class="badge badge-ok">保護中</span>' : '<span class="badge badge-muted">未保護</span>'}</td>
        </tr>`).join('');
    } catch { /* ignore */ }
}

// ── Config ──
let loadedConfig = null;

async function loadConfig() {
    try {
        const [c, sRes, tRes] = await Promise.all([
            api('/config'),
            api('/edcb/services').catch(() => ({ services: [] })),
            api('/edcb/tuners').catch(() => ({ tuners: [] }))
        ]);
        loadedConfig = c;

        document.getElementById('cfgHost').textContent = c.edcb?.host || '-';
        document.getElementById('cfgPort').textContent = c.edcb?.port || '-';
        document.getElementById('cfgRecInterval').textContent = (c.recording?.interval_minutes || '-') + ' 分';
        document.getElementById('cfgRecAdv').textContent = (c.recording?.advance_reserve_count || '-') + ' 枠';
        document.getElementById('cfgRecDir').textContent = c.recording?.output_directory || '-';
        document.getElementById('cfgEewInt').textContent = (c.eew?.poll_interval_minutes || '-') + ' 分';
        document.getElementById('cfgEewRet').textContent = (c.eew?.default_retention_hours || '-') + ' 時間';
        document.getElementById('cfgEewBuf').textContent = (c.eew?.pre_buffer_minutes || '-') + ' 分';

        const chSel = document.getElementById('cfgChSelect');
        const curCh = c.channel || {};
        const curChKey = `${curCh.onid}-${curCh.tsid}-${curCh.sid}`;

        chSel.innerHTML = '<option value="">選択してください</option>';
        if (sRes.services) {
            sRes.services.forEach(s => {
                const key = `${s.onid}-${s.tsid}-${s.sid}`;
                const opt = document.createElement('option');
                opt.value = JSON.stringify(s);
                opt.textContent = s.name;
                if (key === curChKey) opt.selected = true;
                chSel.appendChild(opt);
            });
        }

        const tSel = document.getElementById('cfgTunerSelect');
        tSel.innerHTML = '<option value="0">自動選択</option>';
        if (tRes.tuners) {
            tRes.tuners.forEach(t => {
                const opt = document.createElement('option');
                opt.value = t.id;
                opt.textContent = t.name;
                if (t.id === (curCh.tuner_id || 0)) opt.selected = true;
                tSel.appendChild(opt);
            });
        }
    } catch { /* ignore */ }
}

// ── Modal ──
function initModal() {
    document.getElementById('modalClose').addEventListener('click', closeModal);
    document.getElementById('extendModal').addEventListener('click', e => {
        if (e.target.id === 'extendModal') closeModal();
    });
    document.querySelectorAll('#presetButtons .btn').forEach(b => {
        b.addEventListener('click', () => doExtend(modalEventId, +b.dataset.hours));
    });
    document.getElementById('customExtendBtn').addEventListener('click', () => {
        const h = +document.getElementById('customHours').value;
        if (h > 0) doExtend(modalEventId, h);
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
}

function openModal(id, epicenter, hours) {
    modalEventId = id;
    document.getElementById('modalEventInfo').textContent = epicenter;
    document.getElementById('modalCurrentHours').textContent = hours >= 87600 ? '無期限' : hours + ' 時間';
    document.getElementById('extendModal').classList.add('active');
}

function closeModal() {
    document.getElementById('extendModal').classList.remove('active');
    modalEventId = null;
}

async function doExtend(id, hours) {
    try {
        await api(`/eew_events/${id}/extend`, { method: 'POST', body: JSON.stringify({ hours }) });
        closeModal();
        toast('保存時間を変更しました');
        await loadEEW();
    } catch {
        toast('変更に失敗しました');
    }
}

// ── Refresh ──
function initRefresh() {
    document.getElementById('refreshEEW').addEventListener('click', loadEEW);
    document.getElementById('refreshRecordings').addEventListener('click', loadRecordings);

    document.getElementById('saveChannelBtn').addEventListener('click', async () => {
        const saveBtn = document.getElementById('saveChannelBtn');
        const chVal = document.getElementById('cfgChSelect').value;
        const tunerId = parseInt(document.getElementById('cfgTunerSelect').value, 10);

        if (!chVal) {
            toast('チャンネルを選択してください');
            return;
        }

        try {
            saveBtn.disabled = true;
            const chData = JSON.parse(chVal);
            chData.tuner_id = tunerId;

            await api('/config', {
                method: 'PUT',
                body: JSON.stringify({ key: 'channel', value: chData })
            });
            toast('設定を保存しました。再起動後に反映されます。');
            if (loadedConfig) loadedConfig.channel = chData;
            loadStatus(); // 更新
        } catch {
            toast('保存に失敗しました');
        } finally {
            saveBtn.disabled = false;
        }
    });
}

// ── Toast ──
function toast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.getElementById('toastContainer').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 200); }, 2500);
}

// ── Utils ──
function fmtDt(iso) {
    if (!iso) return '-';
    try {
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        return d.toLocaleString('ja-JP', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
}

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}
