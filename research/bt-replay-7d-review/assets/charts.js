/* bt-replay-7d 回测复盘报告 — 图表脚本（ECharts, SVG renderer, 静态） */
(function () {
  var els = {
    equity: document.getElementById('chart-equity'),
    strategy: document.getElementById('chart-strategy'),
    regime: document.getElementById('chart-regime'),
  };
  if (typeof echarts === 'undefined') return;

  var cs = getComputedStyle(document.documentElement);
  function tok(name, fallback) {
    var v = cs.getPropertyValue(name).trim();
    return v || fallback;
  }
  var SERIES1 = tok('--chart-series-1', '#0969DA');
  var SERIES2 = tok('--chart-series-2', '#8250DF');
  var POS = tok('--chart-positive', '#52C41A');
  var NEG = tok('--chart-negative', '#FF4D4F');
  var AXIS = tok('--chart-axis', '#66727F');
  var GRID = tok('--chart-grid', 'rgba(27,36,48,0.10)');
  var TOOLTIP_BG = tok('--chart-tooltip-bg', '#FFFFFF');
  var INK = tok('--page-text', '#1B2430');

  function baseTooltip() {
    return {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: TOOLTIP_BG,
      borderColor: GRID,
      borderWidth: 1,
      textStyle: { color: INK, fontSize: 12 },
      valueFormatter: function (v) {
        return (v == null ? '—' : Number(v).toFixed(3) + ' USDT');
      },
    };
  }
  function axisCommon() {
    return {
      axisLine: { lineStyle: { color: GRID } },
      axisTick: { show: false },
      axisLabel: { color: AXIS, fontSize: 11 },
      splitLine: { lineStyle: { color: GRID, width: 1 } },
    };
  }
  var charts = [];
  function mount(el, option) {
    if (!el) return;
    var c = echarts.init(el, null, { renderer: 'svg' });
    c.setOption(option);
    charts.push(c);
  }
  window.addEventListener('resize', function () {
    charts.forEach(function (c) { c.resize(); });
  });

  /* ---- 图 1：累计净盈亏曲线 ---- */
  var equityData = [
    [1789909200000, 0.0],
    [1789923600000, 0.085],
    [1789981200000, 0.965],
    [1790017200000, 4.803],
    [1790020800000, 8.642],
    [1790038800000, 6.482],
    [1790168400000, 5.004],
    [1790175600000, 1.308],
    [1790218800000, 1.184],
    [1790240400000, -0.276],
    [1790240400001, -1.366],
    [1790326800000, -0.571],
    [1790337600000, -2.73],
    [1790398800000, -2.558],
    [1790398800001, -2.261],
    [1790503200000, -2.022],
    [1790503200001, -1.641],
  ];
  mount(els.equity, {
    animation: false,
    grid: { left: 56, right: 24, top: 28, bottom: 48 },
    tooltip: Object.assign(baseTooltip(), {
      trigger: 'axis',
      formatter: function (ps) {
        var p = ps[0];
        var t = new Date(p.value[0]);
        var mm = ('0' + (t.getMonth() + 1)).slice(-2);
        var dd = ('0' + t.getDate()).slice(-2);
        var hh = ('0' + t.getHours()).slice(-2);
        var mi = ('0' + t.getMinutes()).slice(-2);
        return mm + '-' + dd + ' ' + hh + ':' + mi + '<br/>累计净盈亏：' +
          Number(p.value[1]).toFixed(3) + ' USDT';
      },
    }),
    xAxis: Object.assign(axisCommon(), {
      type: 'time',
      axisLabel: { color: AXIS, fontSize: 11, formatter: '{MM}-{dd}' },
      splitLine: { show: false },
    }),
    yAxis: Object.assign(axisCommon(), {
      type: 'value',
      name: 'USDT',
      nameTextStyle: { color: AXIS, fontSize: 11 },
    }),
    series: [{
      type: 'line',
      data: equityData,
      smooth: false,
      symbol: 'circle',
      symbolSize: 5,
      itemStyle: { color: SERIES1 },
      lineStyle: { color: SERIES1, width: 2 },
      areaStyle: {
        color: SERIES1,
        opacity: 0.08,
      },
      markLine: {
        silent: true,
        symbol: 'none',
        animation: false,
        lineStyle: { color: AXIS, type: 'dashed', width: 1 },
        label: { show: false },
        data: [{ yAxis: 0 }],
      },
      markPoint: {
        symbolSize: 46,
        animation: false,
        data: [
          { coord: [1790020800000, 8.642], value: '峰值 +8.64', itemStyle: { color: POS }, label: { color: '#fff', fontSize: 10 } },
          { coord: [1790337600000, -2.73], value: '谷底 -2.73', itemStyle: { color: NEG }, label: { color: '#fff', fontSize: 10 } },
        ],
      },
    }],
  });

  /* ---- 图 2：策略净盈亏 vs 手续费 ---- */
  var strategyCats = ['网格 ×12 笔', '做多仓位 ×4 笔'];
  mount(els.strategy, {
    animation: false,
    grid: { left: 64, right: 24, top: 56, bottom: 36 },
    legend: {
      top: 12,
      itemWidth: 12,
      itemHeight: 12,
      textStyle: { color: AXIS, fontSize: 12 },
    },
    tooltip: Object.assign(baseTooltip(), { trigger: 'axis', axisPointer: { type: 'shadow' } }),
    xAxis: Object.assign(axisCommon(), {
      type: 'category',
      data: strategyCats,
      axisLabel: { color: AXIS, fontSize: 12, interval: 0 },
      splitLine: { show: false },
    }),
    yAxis: Object.assign(axisCommon(), {
      type: 'value',
      name: 'USDT',
      nameTextStyle: { color: AXIS, fontSize: 11 },
    }),
    series: [
      {
        name: '净盈亏',
        type: 'bar',
        barWidth: 44,
        itemStyle: { color: SERIES1, borderRadius: 4 },
        data: [-5.0, 3.358],
        label: {
          show: true,
          position: 'outside',
          color: INK,
          fontSize: 11,
          formatter: function (p) { return Number(p.value).toFixed(2); },
        },
      },
      {
        name: '手续费',
        type: 'bar',
        barWidth: 44,
        itemStyle: { color: SERIES2, borderRadius: 4 },
        data: [5.211, 0.0],
        label: {
          show: true,
          position: 'outside',
          color: INK,
          fontSize: 11,
          formatter: function (p) { return Number(p.value).toFixed(2); },
        },
      },
    ],
  });

  /* ---- 图 3：按入场 regime 的净盈亏（横向，正负着色） ---- */
  var regimeCats = ['RANGING（12 笔）', 'TRENDING_BULL（3 笔）', 'BREAKOUT（1 笔）'];
  var regimeVals = [-5.0, -0.48, 3.838];
  mount(els.regime, {
    animation: false,
    grid: { left: 150, right: 56, top: 28, bottom: 36 },
    tooltip: Object.assign(baseTooltip(), { trigger: 'axis', axisPointer: { type: 'shadow' } }),
    xAxis: Object.assign(axisCommon(), {
      type: 'value',
      name: 'USDT',
      nameTextStyle: { color: AXIS, fontSize: 11 },
    }),
    yAxis: Object.assign(axisCommon(), {
      type: 'category',
      data: regimeCats,
      inverse: true,
      axisLabel: { color: AXIS, fontSize: 12 },
      splitLine: { show: false },
    }),
    series: [{
      type: 'bar',
      barWidth: 34,
      data: regimeVals.map(function (v) {
        return {
          value: v,
          itemStyle: { color: v >= 0 ? POS : NEG, borderRadius: v >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] },
        };
      }),
      label: {
        show: true,
        position: 'right',
        color: INK,
        fontSize: 11,
        formatter: function (p) { return Number(p.value).toFixed(2); },
      },
    }],
  });
})();
