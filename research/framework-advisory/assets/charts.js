/* 框架综合分析与改进建议报告 — 图表脚本（ECharts, SVG renderer, 静态） */
(function () {
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
  var OTHER = tok('--chart-other', '#D9E0E8');
  var AXIS = tok('--chart-axis', '#66727F');
  var GRID = tok('--chart-grid', 'rgba(27,36,48,0.10)');
  var TOOLTIP_BG = tok('--chart-tooltip-bg', '#FFFFFF');
  var INK = tok('--page-text', '#1B2430');

  function baseTooltip() {
    return {
      appendToBody: true,
      backgroundColor: TOOLTIP_BG,
      borderColor: GRID,
      borderWidth: 1,
      textStyle: { color: INK, fontSize: 12 },
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

  /* ---- 图 1：框架五维雷达 ---- */
  var radarEl = document.getElementById('chart-radar');
  mount(radarEl, {
    animation: false,
    tooltip: Object.assign(baseTooltip(), {}),
    radar: {
      indicator: [
        { name: '编排稳定性', max: 5 },
        { name: '安全护栏完备', max: 5 },
        { name: '执行层盈利能力', max: 5 },
        { name: '决策质量闭环', max: 5 },
        { name: '验证方法论', max: 5 },
      ],
      radius: '62%',
      center: ['50%', '54%'],
      axisName: { color: AXIS, fontSize: 11 },
      splitLine: { lineStyle: { color: GRID } },
      splitArea: {
        areaStyle: { color: [tok('--page-surface', '#F5F7FA'), tok('--bg', '#FFFFFF')] },
      },
      axisLine: { lineStyle: { color: GRID } },
    },
    series: [{
      type: 'radar',
      symbol: 'circle',
      symbolSize: 5,
      data: [{
        value: [4, 3.5, 2, 3, 2],
        name: '本次评估',
        itemStyle: { color: SERIES1 },
        lineStyle: { color: SERIES1, width: 2 },
        areaStyle: { color: SERIES1, opacity: 0.14 },
        label: {
          show: true,
          color: INK,
          fontSize: 11,
          formatter: function (p) { return Number(p.value).toFixed(1); },
        },
      }],
    }],
  });

  /* ---- 图 2：LONG 决策置信度 vs 结局 ---- */
  var confCats = [
    'BTC 09-21\nBREAKOUT',
    'ETH 09-21\nTREND_BULL',
    'BTC 09-22①\nTREND_BULL',
    'BTC 09-22②\nTREND_BULL',
    'BTC 09-22③\nTREND_BULL',
    'ETH 09-25\nTREND_BULL',
  ];
  var confData = [
    { value: 0.62, outcome: '止盈 +3.84', color: POS },
    { value: 0.60, outcome: '止盈 +3.84', color: POS },
    { value: 0.65, outcome: '未执行（拦截）', color: OTHER },
    { value: 0.62, outcome: '止损 -2.16', color: NEG },
    { value: 0.68, outcome: '未执行（拦截）', color: OTHER },
    { value: 0.62, outcome: '止损 -2.16', color: NEG },
  ];
  mount(document.getElementById('chart-confidence'), {
    animation: false,
    grid: { left: 48, right: 24, top: 40, bottom: 56 },
    tooltip: Object.assign(baseTooltip(), {
      trigger: 'item',
      formatter: function (p) {
        return confCats[p.dataIndex].replace('\n', ' ') +
          '<br/>置信度：' + Number(p.value).toFixed(2) +
          '<br/>结局：' + confData[p.dataIndex].outcome;
      },
    }),
    xAxis: {
      type: 'category',
      data: confCats,
      axisLine: { lineStyle: { color: GRID } },
      axisTick: { show: false },
      axisLabel: { color: AXIS, fontSize: 10, interval: 0, lineHeight: 14 },
    },
    yAxis: {
      type: 'value',
      min: 0.5,
      max: 0.8,
      name: '置信度',
      nameTextStyle: { color: AXIS, fontSize: 11 },
      axisLabel: { color: AXIS, fontSize: 11 },
      splitLine: { lineStyle: { color: GRID, width: 1 } },
    },
    series: [{
      type: 'bar',
      barWidth: 40,
      data: confData.map(function (d) {
        return {
          value: d.value,
          itemStyle: { color: d.color, borderRadius: 4 },
          label: {
            show: true,
            position: 'top',
            color: INK,
            fontSize: 10,
            formatter: d.outcome,
          },
        };
      }),
      markLine: {
        silent: true,
        symbol: 'none',
        animation: false,
        lineStyle: { color: AXIS, type: 'dashed', width: 1 },
        label: { show: false },
        data: [],
      },
    }],
    legend: {
      top: 8,
      itemWidth: 12,
      itemHeight: 12,
      textStyle: { color: AXIS, fontSize: 12 },
      data: [
        { name: '止盈', icon: 'roundRect', itemStyle: { color: POS } },
        { name: '止损', icon: 'roundRect', itemStyle: { color: NEG } },
        { name: '未执行', icon: 'roundRect', itemStyle: { color: OTHER } },
      ],
    },
  });
})();
