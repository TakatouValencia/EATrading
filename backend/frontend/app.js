document.addEventListener('DOMContentLoaded', () => {
    fetchData();
    // Auto refresh every 5 seconds if live
    setInterval(fetchData, 5000);
});

async function fetchData() {
    try {
        const response = await fetch('/api/dashboard');
        if (!response.ok) return;
        const data = await response.json();
        updateDashboard(data);
    } catch (error) {
        console.error('Error fetching dashboard data:', error);
    }
}

let equityChartInstance = null;

function updateDashboard(data) {
    if (!data || !data.summary) return;

    // Update Hero Stats
    document.getElementById('total-trades').textContent = data.summary.total_trades;
    
    const wr = data.summary.win_rate;
    const wrEl = document.getElementById('win-rate');
    wrEl.textContent = wr.toFixed(1) + '%';
    wrEl.className = wr >= 65 ? 'accent-green' : 'accent-red';

    const pnl = data.summary.total_pnl;
    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = (pnl > 0 ? '+' : '') + pnl.toFixed(2) + ' R';
    pnlEl.className = pnl >= 0 ? 'accent-green' : 'accent-red';

    document.getElementById('max-dd').textContent = '-' + data.summary.max_drawdown.toFixed(2) + ' R';

    // Update Active Signals Table
    const activeTbody = document.querySelector('#active-table tbody');
    activeTbody.innerHTML = '';
    
    if (data.active_signals && data.active_signals.length > 0) {
        data.active_signals.forEach(signal => {
            const tr = document.createElement('tr');
            
            const dateObj = new Date(signal.time.replace('Z', '+00:00'));
            const dateStr = dateObj.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
            
            const badgeClass = signal.status === 'PENDING' ? 'pending' : 'active';
            
            tr.innerHTML = `
                <td>${dateStr}</td>
                <td>${signal.type.split(' ')[0]}</td>
                <td>${signal.entry.toFixed(1)}</td>
                <td>${signal.sl.toFixed(1)}</td>
                <td>${signal.tp.toFixed(1)}</td>
                <td><span class="badge ${badgeClass}">${signal.status}</span></td>
            `;
            activeTbody.appendChild(tr);
        });
    } else {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td colspan="6" style="text-align:center; color: var(--text-muted)">No active or pending signals</td>`;
        activeTbody.appendChild(tr);
    }

    // Update Recent Trades Table
    const tbody = document.querySelector('#trades-table tbody');
    tbody.innerHTML = '';
    
    data.recent_trades.forEach(trade => {
        const tr = document.createElement('tr');
        
        // Format date
        const dateObj = new Date(trade.time.replace('Z', '+00:00'));
        const dateStr = dateObj.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        
        // Badge
        const badgeClass = trade.status === 'WIN' ? 'win' : 'loss';
        const pnlText = trade.pnl > 0 ? `+${trade.pnl.toFixed(2)} R` : `${trade.pnl.toFixed(2)} R`;

        tr.innerHTML = `
            <td>${dateStr}</td>
            <td>${trade.type.split(' ')[0]}</td>
            <td><span class="badge ${badgeClass}">${trade.status}</span></td>
            <td class="${trade.pnl > 0 ? 'accent-green' : 'accent-red'}">${pnlText}</td>
        `;
        tbody.appendChild(tr);
    });

    // Update Chart
    updateChart(data.equity_curve);
}

function updateChart(equityData) {
    const ctx = document.getElementById('equityChart').getContext('2d');
    
    const labels = equityData.map(d => {
        const date = new Date(d.time.replace('Z', '+00:00'));
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });
    
    const dataPoints = equityData.map(d => d.equity);

    if (equityChartInstance) {
        equityChartInstance.data.labels = labels;
        equityChartInstance.data.datasets[0].data = dataPoints;
        equityChartInstance.update();
        return;
    }

    // Create gradient
    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(0, 255, 157, 0.5)');
    gradient.addColorStop(1, 'rgba(0, 255, 157, 0.0)');

    equityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Equity (R)',
                data: dataPoints,
                borderColor: '#00ff9d',
                backgroundColor: gradient,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 6,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(20, 22, 30, 0.9)',
                    titleColor: '#8e95a5',
                    bodyColor: '#f0f2f5',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#8e95a5', maxTicksLimit: 10 }
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#8e95a5' }
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    });
}
