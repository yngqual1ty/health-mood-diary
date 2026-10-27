/*
 * k6 load test: Telegram Mini App (TMA) patient HTTP session.
 *
 * Mirrors frontend/lib/api.ts: Authorization: tma <initData>
 * (not the doctor Login Widget / Bearer cookie).
 *
 * Run against a local API (uvicorn app:app --port 8000). Do not point this
 * at production (https://bot.koni.moscow) unless you intend a real load test.
 *
 *   k6 run -e BASE_URL=http://127.0.0.1:8000 -e BOT_TOKEN="$BOT_TOKEN" load/tma.js
 *
 * Optional env:
 *   VUS              default 10
 *   DURATION         default 1m  (hold time after a short ramp)
 *   TELEGRAM_ID      shared test user (otherwise 2000000000 + __VU)
 *   TELEGRAM_ID_BASE default 2000000000  (used when TELEGRAM_ID is unset)
 *
 * Auth: BOT_TOKEN signs synthetic WebApp initData the same way Telegram does
 * and the same way auth.py validate_init_data() checks it:
 *   secret = HMAC_SHA256(key="WebAppData", msg=bot_token)
 *   hash   = hex(HMAC_SHA256(key=secret, msg=data_check_string))
 * data_check_string is sorted "key=value" lines (excluding hash), newline-joined.
 *
 * Dev bypass (not used here): if DEV_TELEGRAM_ID > 0 and the request has no
 * tma / Bearer / session cookie, app.py current_user() impersonates that id.
 * Leave it unset in production. Prefer real tma HMAC so this matches TMA.
 *
 * Side effect: completed HADS / ОСР / allergies / pills / mood may trigger
 * outbound Bot API sendMessage (TELEGRAM_PROXY is a Bot API base URL, not an
 * HTTP proxy). Fake telegram_ids will 400 from Telegram; that is in-process
 * on the API and can add latency. Lowest valid questionnaire scores are sent
 * so doctor-alert / hotline thresholds are not tripped.
 */

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import crypto from 'k6/crypto';

const BASE_URL = (__ENV.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const BOT_TOKEN = __ENV.BOT_TOKEN || '';
const VUS = Number(__ENV.VUS || 10) || 10;
const DURATION = __ENV.DURATION || '1m';
const TELEGRAM_ID_BASE = Number(__ENV.TELEGRAM_ID_BASE || 2000000000) || 2000000000;

export const options = {
  stages: [
    { duration: '10s', target: 50 },
    { duration: '30s', target: 100 },
    { duration: '30s', target: 200 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000'],
  },
};

export function setup() {
  if (!BOT_TOKEN) {
    throw new Error(
      'BOT_TOKEN is required to HMAC-sign Telegram WebApp initData. ' +
        'Example: k6 run -e BASE_URL=http://127.0.0.1:8000 -e BOT_TOKEN="$BOT_TOKEN" load/tma.js',
    );
  }
  return { botToken: BOT_TOKEN, baseUrl: BASE_URL };
}

function think() {
  sleep(0.5 + Math.random() * 1.5);
}

function jsonHeaders(initData) {
  return {
    Authorization: `tma ${initData}`,
    'Content-Type': 'application/json',
  };
}

/**
 * Telegram Mini App initData HMAC (WebAppData), matching auth.py.
 */
function signInitData(botToken, telegramId, vu) {
  const authDate = String(Math.floor(Date.now() / 1000));
  const user = JSON.stringify({
    id: telegramId,
    first_name: 'Load',
    last_name: `VU${vu}`,
    username: `loadtest_${telegramId}`,
    language_code: 'ru',
  });
  const params = {
    auth_date: authDate,
    user: user,
  };
  const keys = Object.keys(params).sort();
  const lines = [];
  for (let i = 0; i < keys.length; i++) {
    const key = keys[i];
    lines.push(`${key}=${params[key]}`);
  }
  const dataCheckString = lines.join('\n');
  const secret = crypto.hmac('sha256', 'WebAppData', botToken, 'binary');
  const hash = crypto.hmac('sha256', secret, dataCheckString, 'hex');
  return `auth_date=${authDate}&user=${encodeURIComponent(user)}&hash=${hash}`;
}

function req(method, path, body, headers, name) {
  const url = `${BASE_URL}${path}`;
  const params = { headers: headers, tags: { name: name || `${method} ${path}` } };
  if (method === 'GET') {
    return http.get(url, params);
  }
  return http.post(url, body ? JSON.stringify(body) : null, params);
}

function ok(res, label, status) {
  const expected = status || 200;
  return check(res, {
    [`${label} ${expected}`]: (r) => r.status === expected,
  });
}

function parseJson(res) {
  try {
    return res.json();
  } catch (e) {
    return null;
  }
}

/** Pick the lowest integer option per item so payloads stay valid (no 422). */
function answersFromDefinition(definition) {
  const answers = {};
  const items = (definition && definition.items) || [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const options = item.options || [];
    let minVal = null;
    for (let j = 0; j < options.length; j++) {
      const value = options[j].value;
      if (typeof value === 'number' && (minVal === null || value < minVal)) {
        minVal = value;
      }
    }
    if (minVal === null) {
      throw new Error(`No integer option for ${item.id}`);
    }
    answers[item.id] = minVal;
  }
  return answers;
}

function submitScale(headers, questionnaireId) {
  group(`questionnaire ${questionnaireId}`, function () {
    const defRes = req(
      'GET',
      `/api/questionnaires/${questionnaireId}`,
      null,
      headers,
      'GET /api/questionnaires/{id}',
    );
    if (!ok(defRes, `${questionnaireId} GET`, 200)) {
      return;
    }
    const definition = parseJson(defRes);
    think();
    const postRes = req(
      'POST',
      '/api/answers',
      {
        questionnaire_id: questionnaireId,
        answers: answersFromDefinition(definition),
        status: 'completed',
      },
      headers,
      'POST /api/answers',
    );
    ok(postRes, `${questionnaireId} POST`, 200);
  });
}

export default function (data) {
  const vu = __VU;
  const telegramId = __ENV.TELEGRAM_ID
    ? Number(__ENV.TELEGRAM_ID)
    : TELEGRAM_ID_BASE + vu;
  const initData = signInitData(data.botToken, telegramId, vu);
  const headers = jsonHeaders(initData);

  let opened = false;
  group('open TMA', function () {
    const me = req('GET', '/api/me', null, headers, 'GET /api/me');
    ok(me, 'GET /api/me', 200);
    if (me.status !== 200) {
      console.error(`VU ${vu} telegram_id=${telegramId} GET /api/me failed: ${me.status} ${me.body}`);
      return;
    }
    opened = true;
    think();

    const results = req('GET', '/api/me/results', null, headers, 'GET /api/me/results');
    ok(results, 'GET /api/me/results', 200);
    think();

    const list = req('GET', '/api/questionnaires', null, headers, 'GET /api/questionnaires');
    ok(list, 'GET /api/questionnaires', 200);
    think();

    const allergies = req('GET', '/api/allergies', null, headers, 'GET /api/allergies');
    ok(allergies, 'GET /api/allergies', 200);
  });

  if (!opened) {
    return;
  }

  think();
  submitScale(headers, 'suicide-risk');
  think();
  submitScale(headers, 'hads');

  group('mood', function () {
    think();
    const listed = req('GET', '/api/moods', null, headers, 'GET /api/moods');
    ok(listed, 'GET /api/moods', 200);
    think();
    const created = req(
      'POST',
      '/api/moods',
      { score: 3, emotions: [], note: '' },
      headers,
      'POST /api/moods',
    );
    ok(created, 'POST /api/moods', 200);
  });

  group('sleep', function () {
    think();
    const listed = req('GET', '/api/sleep', null, headers, 'GET /api/sleep');
    ok(listed, 'GET /api/sleep', 200);
    const body = parseJson(listed) || {};
    think();
    const sleepPayload = { quality: 'ok', hours: 7.5, note: 'k6' };
    if (body.today) {
      sleepPayload.date = body.today;
    }
    const saved = req(
      'POST',
      '/api/sleep',
      sleepPayload,
      headers,
      'POST /api/sleep',
    );
    ok(saved, 'POST /api/sleep', 200);
  });

  group('allergies', function () {
    think();
    const saved = req(
      'POST',
      '/api/allergies',
      { text: 'k6: none' },
      headers,
      'POST /api/allergies',
    );
    ok(saved, 'POST /api/allergies', 200);
  });

  group('pills', function () {
    think();
    const listed = req('GET', '/api/pills', null, headers, 'GET /api/pills');
    ok(listed, 'GET /api/pills', 200);
    think();
    const saved = req(
      'POST',
      '/api/pills',
      {
        items: [{ name: 'k6-vit', time: 'утро', note: '1 tab' }],
        confirm: true,
      },
      headers,
      'POST /api/pills',
    );
    ok(saved, 'POST /api/pills', 200);
    if (saved.status === 200) {
      think();
      const log = req(
        'POST',
        '/api/pills/log',
        { taken: 'yes', note: '' },
        headers,
        'POST /api/pills/log',
      );
      ok(log, 'POST /api/pills/log', 200);
    }
  });
}
