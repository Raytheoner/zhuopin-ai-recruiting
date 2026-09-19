// 候选人答题端（voice-structured-interview U4 tasks 5.8）。全部用相对路径
// 调用当前 origin 上的语音主机接口——语音主机不挂在任何子路径前缀下（与
// `.51` 的 root_path 约束是两码事：那是 `.51` 自己的部署约束，语音主机是
// 独立域名/IP，本页面天然满足"相对路径"要求，不需要额外处理 root_path）。
// ⛔ 本页面不访问 `.51` 的任何接口（design D12/D19）。

const params = new URLSearchParams(window.location.search);
const sessionId = params.get("session_id");

const questionTextEl = document.getElementById("question-text");
const switchTextBtn = document.getElementById("switch-text-btn");
const textAnswerPanel = document.getElementById("text-answer-panel");
const textAnswerInput = document.getElementById("text-answer-input");
const submitTextBtn = document.getElementById("submit-text-btn");
const replayBtn = document.getElementById("replay-btn");
const networkPromptEl = document.getElementById("network-quality-prompt");

if (!sessionId) {
  questionTextEl.textContent = "缺少场次标识，请通过邀约链接重新进入。";
} else {
  questionTextEl.textContent = "题目将通过语音播报，同时在此同步显示文字（AI 生成内容）。";
}

async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    throw new Error(`请求失败: ${response.status}`);
  }
  return response.json();
}

switchTextBtn.addEventListener("click", async () => {
  await postJson(`/sessions/${sessionId}/switch-to-text`, { trigger: "manual" });
  textAnswerPanel.hidden = false;
});

submitTextBtn.addEventListener("click", async () => {
  const text = textAnswerInput.value.trim();
  if (!text) {
    return;
  }
  await postJson(`/sessions/${sessionId}/text-answer`, { text });
  textAnswerInput.value = "";
});

replayBtn.addEventListener("click", () => {
  // 真实实现：向房间发一条 LiveKit data channel 消息触发 agents worker 侧
  // 的重听逻辑（不计入追问次数，spec Scenario「候选人请求重听」）。本文件
  // 只交付页面骨架与文本降级通道；LiveKit 房间连接与音频播放的接线属于
  // 现场联调范围（0.4 主机到位后），不在本计划的自动化测试范围内。
  console.log("重听请求已发送（占位：真实实现走 LiveKit data channel）");
});
