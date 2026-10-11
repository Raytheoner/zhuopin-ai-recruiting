"""Task 9: execute the page script against browser and real route contracts."""
import json
from pathlib import Path
import re
import subprocess

import pytest

PAGE = Path(__file__).resolve().parents[1] / 'app/web/static/invitation_review.html'


@pytest.fixture
def invitation_api(make_test_client):
    from app.storage.hr_account import upsert_account

    client, conn = make_test_client()
    upsert_account(conn, username="hr-page-test", password="secret")
    client.post("/api/auth/login", json={"username": "hr-page-test", "password": "secret"})
    conn.execute("INSERT INTO job (id, title) VALUES ('j-page', '工程师')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c-page', '测试')")
    conn.execute("INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) VALUES ('r-page', 'j-page', 'synthetic', 'a.pdf', 'sha-page', 'hr-page-test')")
    conn.execute("INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) VALUES ('a-page', 'c-page', 'j-page', 'r-page', 'interview')")
    conn.execute("INSERT INTO interview_slot (id, application_id, round, start_at, end_at, mode) VALUES ('s-page', 'a-page', 1, '2026-10-12 14:00', '2026-10-12 15:00', 'onsite')")
    conn.commit()
    return client, conn


@pytest.mark.parametrize('mode, response, expected', [
    ('blocked', {'delivery_mode':'blocked','blocked_reason':'开关关闭'}, '开关关闭'),
    ('allowed', {'delivery_mode':'allowed','message_id':'msg-1'}, '本次外发已放行'),
    ('replay', {'delivery_mode':'replay','message_id':'msg-1'}, '已有投递事实'),
])
@pytest.mark.parametrize('prefix', ['', '/hr/recruit-agent', '/another/mount'])
def test_page_requests_slot_under_base_and_keeps_ai_body(prefix, mode, response, expected):
    html = PAGE.read_text()
    assert '<!--BASE_HREF-->' in html
    assert 'href="static/app.css"' in html
    script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
    harness = r'''
const assert = require('node:assert/strict');
Array.prototype.at = undefined;
const elements = {};
global.document = {
  getElementById: id => elements[id] ||= {textContent: '', value: '', appendChild() {}},
  createElement: () => ({})
};
global.location = {pathname: PATH};
const calls = [];
global.fetch = async (url, options) => {
  const method = options?.method || 'GET';
  calls.push({url, method});
  let payload;
  if (method === 'GET' && url === 'api/interview-slots/s1/invitation') payload = {
    slot: {application_id:'a', round:1, start_at:'start', end_at:'end', status:'scheduled', invitation_status:'pending'},
    drafts: [{id:'d', version:1, template_version:'v1', ai_generated:true, body:'AI 生成标识\n邀约正文'}],
    outcomes: []
  };
  else if (method === 'POST' && url === 'api/interview-slots/s1/invitation/send') payload = {
    delivery_mode: MODE, blocked_reason:'开关关闭'
  };
  else throw new Error(`Unexpected request: ${method} ${url}`);
  return {ok: true, json: async () => payload};
};
'''.replace('PATH', json.dumps(prefix + '/interview-slots/s1/invitation')).replace('MODE', json.dumps(mode)).replace(
        "{delivery_mode: MODE, blocked_reason:'开关关闭'}", json.dumps(response)
    )
    checks = r'''
setImmediate(async () => {
  assert.deepEqual(calls[0], {url:'api/interview-slots/s1/invitation', method:'GET'});
  assert.equal(elements['draft-body'].textContent, 'AI 生成标识\n邀约正文');
  await elements['send-by-system'].onclick();
  assert.deepEqual(calls[1], {url:'api/interview-slots/s1/invitation/send', method:'POST'});
  assert.ok(elements['status-line'].textContent.includes(EXPECTED));
  assert.ok(calls.every(call => !call.url.startsWith('/')));
});
'''.replace('EXPECTED', json.dumps(expected))
    result = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_page_actions_use_their_route_contracts():
    html = PAGE.read_text()
    script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
    harness = r'''
const assert = require('node:assert/strict');
Array.prototype.at = undefined;
const elements = {};
global.document = {
  getElementById: id => elements[id] ||= {textContent: '', value: 'sample', children: [], appendChild(child) { this.children.push(child); }},
  createElement: () => ({})
};
global.location = {pathname:'/hr/recruit-agent/interview-slots/s1/invitation'};
const calls = [];
global.fetch = async (url, options) => {
  const method = options?.method || 'GET';
  const body = options?.body ? JSON.parse(options.body) : null;
  calls.push({url, method, body});
  let data;
  if (method === 'GET' && url === 'api/interview-slots/s1/invitation') data = {
    slot:{application_id:'a',round:1,start_at:'x',end_at:'y',status:'scheduled',invitation_status:'pending'},
    template:{version:'v1',updated_by:'hr',body:'T'},
    drafts:[{id:'d',version:1,template_version:'v1',ai_generated:true,body:'AI 生成标识\n正文'}],
    outcomes:[{status:'confirmed',channel:'wechat',reason:null,actor:'hr',at:'now'}]
  };
  else if (method === 'PUT' && url === 'api/invitation-templates') data = {version:'v2',updated_by:'hr',body:'new template'};
  else if (method === 'POST' && url === 'api/interview-slots/s1/invitation/generate') data = {
    slot:{application_id:'a',round:1,start_at:'x',end_at:'y',status:'scheduled',invitation_status:'pending'},
    template:{version:'v1',updated_by:'hr',body:'T'}, drafts:[], outcomes:[], created_draft_id:'d2'
  };
  else if (method === 'POST' && url === 'api/interview-slots/s1/invitation/drafts/d/edit') data = {draft_id:'d',body:'AI 生成标识\n编辑正文'};
  else if (method === 'POST' && url === 'api/interview-slots/s1/invitation/drafts/d/mark-human-written') data = {draft_id:'d',body:'人工正文'};
  else if (method === 'POST' && url === 'api/interview-slots/s1/invitation/outcome') data = {
    outcome:{status:'confirmed',actor:'hr',at:'now'},
    slot:{application_id:'a',round:1,start_at:'x',end_at:'y',status:'scheduled',invitation_status:'confirmed'},
    template:{version:'v1',updated_by:'hr',body:'T'}, drafts:[], outcomes:[{status:'confirmed',channel:'wechat',reason:null,actor:'hr',at:'now'}]
  };
  else if (method === 'POST' && url === 'api/interview-slots/s1/invitation/send') data = {delivery_mode:'blocked',blocked_reason:'开关关闭'};
  else throw new Error(`Unexpected request: ${method} ${url}`);
  return {ok:true,json:async()=>data};
};
'''
    checks = r'''
setImmediate(async () => {
  elements['template-body'].value = 'new template';
  await elements['save-template'].onclick();
  assert.equal(elements['status-line'].textContent, '模板已保存为 v2');
  await elements['generate'].onclick();
  assert.equal(elements['status-line'].textContent, '已生成新版本');
  await elements['save-edit'].onclick();
  assert.equal(elements['status-line'].textContent, '已保存（AI 标识保留）');
  await elements['mark-human'].onclick();
  assert.equal(elements['status-line'].textContent, '已标记为人工撰写（已留痕）');
  await elements['submit-outcome'].onclick();
  assert.equal(elements['status-line'].textContent, '已回填');
  await elements['send-by-system'].onclick();
  assert.deepEqual(calls[0], {url:'api/interview-slots/s1/invitation', method:'GET', body:null});
  assert.deepEqual(calls[1], {url:'api/invitation-templates', method:'PUT', body:{body:'new template'}});
  assert.deepEqual(calls[2], {url:'api/interview-slots/s1/invitation', method:'GET', body:null});
  assert.deepEqual(calls[3], {url:'api/interview-slots/s1/invitation/generate', method:'POST', body:{}});
  assert.deepEqual(calls[4], {url:'api/interview-slots/s1/invitation', method:'GET', body:null});
  assert.deepEqual(calls[5], {url:'api/interview-slots/s1/invitation/drafts/d/edit', method:'POST', body:{body:'AI 生成标识\n正文'}});
  assert.deepEqual(calls[6], {url:'api/interview-slots/s1/invitation', method:'GET', body:null});
  assert.deepEqual(calls[7], {url:'api/interview-slots/s1/invitation/drafts/d/mark-human-written', method:'POST', body:{}});
  assert.deepEqual(calls[8], {url:'api/interview-slots/s1/invitation', method:'GET', body:null});
  assert.deepEqual(calls[9], {url:'api/interview-slots/s1/invitation/outcome', method:'POST', body:{status:'sample', channel:'sample', reason:'sample'}});
  assert.deepEqual(calls[10], {url:'api/interview-slots/s1/invitation', method:'GET', body:null});
  assert.deepEqual(calls[11], {url:'api/interview-slots/s1/invitation/send', method:'POST', body:{}});
  assert.equal(elements['status-line'].textContent, '外发被门禁拦截：开关关闭');
  assert.ok(elements['outcome-list'].children[0].textContent.includes('confirmed'));
  assert.ok(calls.every(c => !c.url.startsWith('/')));
});
'''
    result = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_real_invitation_route_draft_body_is_rendered_without_stripping_ai_label(invitation_api, monkeypatch):
    from app.agents.invitation_drafter import InvitationDraft
    from app.agents.jd_agent import AI_LABEL_PREFIX, enforce_ai_label

    client, _ = invitation_api
    draft = InvitationDraft(
        body=enforce_ai_label('真实路由邀约正文', generated_at='2026-10-10T00:00:00+00:00'),
        run_id='page-run', response_model='actual-v1', prompt_version='invite-v1',
    )
    monkeypatch.setattr(
        'app.web.server.compute_invitation_draft_for_slot',
        lambda *args, **kwargs: (draft, 'v1'),
    )
    generated = client.post('/api/interview-slots/s-page/invitation/generate', json={})
    assert generated.status_code == 201
    routed = client.get('/api/interview-slots/s-page/invitation')
    assert routed.status_code == 200
    route_payload = routed.json()
    body = route_payload['drafts'][0]['body']
    assert AI_LABEL_PREFIX in body
    assert '真实路由邀约正文' in body

    html = PAGE.read_text()
    script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
    harness = r'''
const assert = require('node:assert/strict');
Array.prototype.at = undefined;
const elements = {};
global.document = {
  getElementById: id => elements[id] ||= {textContent:'', value:'', children:[], appendChild(child) { this.children.push(child); }},
  createElement: () => ({})
};
global.location = {pathname:'/hr/recruit-agent/interview-slots/s-page/invitation'};
const routePayload = ROUTE_PAYLOAD;
const calls = [];
global.fetch = async (url, options) => {
  calls.push({url, method:options?.method || 'GET'});
  assert.equal(url, 'api/interview-slots/s-page/invitation');
  assert.equal(options?.method || 'GET', 'GET');
  return {ok:true, json:async()=>routePayload};
};
'''.replace('ROUTE_PAYLOAD', json.dumps(route_payload, ensure_ascii=False))
    checks = r'''
setImmediate(() => {
  assert.equal(calls.length, 1);
  assert.equal(elements['draft-body'].textContent, EXPECTED_BODY);
  assert.equal(elements['edit-body'].value, EXPECTED_BODY);
  assert.ok(elements['draft-list'].children[0].textContent.includes('AI 生成'));
});
'''.replace('EXPECTED_BODY', json.dumps(body, ensure_ascii=False))
    result = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_page_render_replaces_base_for_mount():
    from app.web.server import _render_static_page

    response = _render_static_page("invitation_review.html", "/hr/recruit-agent")
    html = response.body.decode()
    assert '<base href="/hr/recruit-agent/">' in html
    assert '<!--BASE_HREF-->' not in html
