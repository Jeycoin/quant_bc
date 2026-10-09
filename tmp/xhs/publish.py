import json, urllib.request

payload = {
  "jsonrpc": "2.0", "id": 5, "method": "tools/call",
  "params": {"name": "publish_content", "arguments": {
    "title": "养了只AI帮我盯盘,它比我还怂",
    "content": (
      "最近给电脑里养了只 AI 打工仔,工作内容是 24 小时盯盘 + 自己做交易研究📈\n\n"
      "说出来你们可能不信,这位仁兄每 30 分钟开一次决策会,雷打不动。一周开了 1626 次会,六成的结论是:「再看看」😅\n\n"
      "它盯的都是大饼二饼这些老熟人。旁边还坐着个风控老妈子:手续费划不划算?仓位超没超?不符合直接一票否决。一周驳回它 293 次,AI 想 all in?门都没有。\n\n"
      "前阵子那一波跳水,我自己的小心脏突突的。它倒好,全程防守姿态,硬是没让我吃面。虽然也没赚到什么大钱就是了🥲\n\n"
      "最离谱的是它还会写交易日记,每笔单子都自己复盘:「本次决策质量:良好,主要失误:入场偏早。」比我老板还严格。\n\n"
      "养 AI 的尽头,是养了个教导主任。\n\n"
      "(模拟盘研究日常,非实盘,纯记录)"
    ),
    "images": ["D:/kimiProjects/quant/tmp/xhs/img1_equity.png", "D:/kimiProjects/quant/tmp/xhs/img2_decisions.png"],
    "tags": ["程序员日常", "AI", "人工智能", "量化交易", "炒股日常", "学习笔记"],
    "is_original": True
  }}
}
req = urllib.request.Request(
    "http://localhost:18060/mcp",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Mcp-Session-Id": "ZXQHDHQRCJLNHJVHZBVPBZTTNJ"},
)
resp = urllib.request.urlopen(req, timeout=300).read().decode("utf-8")
print(resp[:2000])
