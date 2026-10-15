(function(){
  'use strict';
  var KEY = new URLSearchParams(location.search).get('k') || '';
  var $ = function(id){ return document.getElementById(id); };
  var chats = {};          // id -> {info, after, feed, inner, tools:{}, perms:{}, live, polling}
  var active = null, histAll = [], atts = [], sending = false;
  var prefs = {}, models = [], commandList = [], usage = {}, cmdSel = 0, panelTimer = null;
  var MODES_UI = [
    ['auto', 'Auto', 'Claude ตัดสินใจเรื่องขออนุญาตเอง', true],
    ['default', 'Manual', 'ถามก่อนทุกครั้งที่จะแก้หรือรันคำสั่ง'],
    ['acceptEdits', 'Accept edits', 'แก้ไฟล์ได้เลย ไม่ต้องถาม'],
    ['plan', 'Plan', 'วางแผนก่อนลงมือ'],
    ['bypassPermissions', 'Bypass permissions', 'อนุญาตทุกอย่างโดยไม่ถาม']
  ];
  var EFFORT_TH = { '':'ค่าเริ่มต้น', low:'Low', medium:'Medium', high:'High', xhigh:'XHigh', max:'Max' };

  // ── helpers ──
  function esc(s){ return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  function api(path, body){
    var opt = { headers:{ 'X-Key':KEY } };
    if(body !== undefined){ opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }
  // the page CSP blocks style="" attributes; bar widths go through the CSSOM instead
  function widths(root){ Array.prototype.forEach.call(root.querySelectorAll('[data-w]'), function(n){ n.style.width = n.getAttribute('data-w') + '%'; }); }
  function el(tag, cls, html){ var e = document.createElement(tag); if(cls) e.className = cls; if(html != null) e.innerHTML = html; return e; }
  function shortPath(p){ p = String(p || ''); var parts = p.split(/[\\/]/); return parts.length > 3 ? '…/' + parts.slice(-3).join('/') : p; }
  function hint(t){ $('hint').textContent = t || ''; }

  // ── markdown (escape first, then a small safe subset) ──
  function inline(s){
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function(_, c){ codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
    s = esc(s);
    s = s.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    return s.replace(/\u0000(\d+)\u0000/g, function(_, i){ return '<code>' + esc(codes[+i]) + '</code>'; });
  }
  function md(src){
    var out = [], lines = String(src || '').replace(/\r/g, '').split('\n'), i = 0;
    while(i < lines.length){
      var ln = lines[i];
      var fence = ln.match(/^\s*```(\S*)/);
      if(fence){
        var buf = []; i++;
        while(i < lines.length && !/^\s*```/.test(lines[i])){ buf.push(lines[i]); i++; }
        i++;
        out.push('<pre><button class="copy" type="button">ก๊อป</button><code>' + esc(buf.join('\n')) + '</code></pre>');
        continue;
      }
      if(/^\s*$/.test(ln)){ i++; continue; }
      var h = ln.match(/^(#{1,4})\s+(.*)/);
      if(h){ out.push('<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>'); i++; continue; }
      if(/^\s*(-{3,}|\*{3,})\s*$/.test(ln)){ out.push('<hr>'); i++; continue; }
      if(/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])){
        var rows = [];
        while(i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])){ rows.push(lines[i]); i++; }
        var cells = function(r){ return r.trim().replace(/^\||\|$/g, '').split('|').map(function(c){ return c.trim(); }); };
        var head = cells(rows[0]), body = rows.slice(2);
        out.push('<table><thead><tr>' + head.map(function(c){ return '<th>' + inline(c) + '</th>'; }).join('') + '</tr></thead><tbody>'
          + body.map(function(r){ return '<tr>' + cells(r).map(function(c){ return '<td>' + inline(c) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table>');
        continue;
      }
      if(/^\s*>/.test(ln)){
        var q = [];
        while(i < lines.length && /^\s*>/.test(lines[i])){ q.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
        out.push('<blockquote>' + inline(q.join(' ')) + '</blockquote>');
        continue;
      }
      var li = ln.match(/^\s*([-*]|\d+[.)])\s+/);
      if(li){
        var ordered = /\d/.test(li[1]), items = [];
        while(i < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[i])){
          var item = lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, ''); i++;
          while(i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])){ item += ' ' + lines[i].trim(); i++; }
          items.push('<li>' + inline(item) + '</li>');
        }
        out.push(ordered ? '<ol>' + items.join('') + '</ol>' : '<ul>' + items.join('') + '</ul>');
        continue;
      }
      var p = [];
      while(i < lines.length && lines[i].trim() && !/^\s*(```|#{1,4}\s|>|[-*]\s|\d+[.)]\s|\|)/.test(lines[i])){ p.push(lines[i]); i++; }
      if(!p.length){ p.push(lines[i]); i++; }
      out.push('<p>' + p.map(inline).join('<br>') + '</p>');
    }
    return out.join('');
  }

  // ── tools ──
  var I = {
    file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
    edit:'<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    term:'<path d="M4 17l6-5-6-5"/><path d="M12 19h8"/>',
    search:'<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
    web:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    bot:'<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4M9 14h.01M15 14h.01"/>',
    list:'<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    plug:'<path d="M9 7V2M15 7V2M6 7h12v5a6 6 0 0 1-12 0zM12 18v4"/>',
    spark:'<path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z"/>'
  };
  function svg(p, n){ return '<svg viewBox="0 0 24 24" width="' + (n || 14) + '" height="' + (n || 14) + '" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + p + '</svg>'; }
  function toolMeta(name, inp){
    inp = inp || {};
    var m = {
      Read:['อ่านไฟล์', I.file, shortPath(inp.file_path)], Write:['เขียนไฟล์', I.edit, shortPath(inp.file_path)],
      Edit:['แก้ไฟล์', I.edit, shortPath(inp.file_path)], MultiEdit:['แก้ไฟล์', I.edit, shortPath(inp.file_path)],
      NotebookEdit:['แก้ notebook', I.edit, shortPath(inp.notebook_path)],
      Bash:['รันคำสั่ง', I.term, inp.description || inp.command], PowerShell:['รันคำสั่ง', I.term, inp.description || inp.command],
      Grep:['ค้นในไฟล์', I.search, (inp.pattern || '') + (inp.path ? '  ใน ' + shortPath(inp.path) : '')],
      Glob:['หาไฟล์', I.search, inp.pattern], WebFetch:['เปิดเว็บ', I.web, inp.url], WebSearch:['ค้นเว็บ', I.web, inp.query],
      Agent:['ส่งงานให้ตัวช่วย', I.bot, inp.description], Task:['ส่งงานให้ตัวช่วย', I.bot, inp.description],
      TodoWrite:['รายการงาน', I.list, (Array.isArray(inp.todos) ? inp.todos.length : 0) + ' ข้อ'], Skill:['ใช้ skill', I.spark, inp.skill || inp.command],
      ExitPlanMode:['เสนอแผน', I.list, ''], AskUserQuestion:['ถามคำถาม', I.spark, '']
    }[name];
    if(m) return m;
    if(/^mcp__/.test(name)) return ['เครื่องมือ', I.plug, name.replace(/^mcp__/, '').split('__').join(' / ')];
    return [name, I.spark, ''];
  }
  function toolBody(name, inp){
    inp = inp || {}; var h = '';
    if(name === 'Edit' || name === 'MultiEdit'){
      var edits = Array.isArray(inp.edits) ? inp.edits : [{ old_string:inp.old_string, new_string:inp.new_string }];
      if(inp.replace_all) h += '<div class="errbox">แทนที่ทุกจุดที่เจอในไฟล์ ไม่ใช่จุดเดียว</div>';
      edits.forEach(function(e){ e = e || {}; h += (e.replace_all ? '<div class="errbox">แทนที่ทุกจุดที่เจอ</div>' : '') + '<div class="lbl">เดิม</div><pre class="del">' + esc(e.old_string) + '</pre><div class="lbl">ใหม่</div><pre class="add">' + esc(e.new_string) + '</pre>'; });
    } else if(name === 'Write'){
      h = '<div class="lbl">เนื้อหา</div><pre>' + esc(inp.content) + '</pre>';
    } else if(name === 'Bash' || name === 'PowerShell'){
      h = '<div class="lbl">คำสั่ง</div><pre>' + esc(inp.command) + '</pre>';
    } else if(name === 'TodoWrite'){
      h = '<ul class="todo">' + (Array.isArray(inp.todos) ? inp.todos : []).map(function(t){ t = t || {};
        var st = t.status === 'completed' ? 'done' : (t.status === 'in_progress' ? 'now' : '');
        return '<li class="' + st + '">' + (st === 'done' ? '&#10003;' : st === 'now' ? '&#9656;' : '&#9675;') + ' ' + esc(t.content || t.activeForm) + '</li>';
      }).join('') + '</ul>';
    } else if(name === 'ExitPlanMode'){
      h = '<div class="md">' + md(inp.plan) + '</div>';
    } else {
      h = '<div class="lbl">ข้อมูลที่ส่ง</div><pre>' + esc(JSON.stringify(inp, null, 2)) + '</pre>';
    }
    return h;
  }

  // ── chats ──
  function ensureChat(info){
    var c = chats[info.id];
    if(!c){
      var feed = el('div', 'feed'), inner = el('div', 'feed-in');
      feed.appendChild(inner); feed.hidden = true; $('feeds').appendChild(feed);
      c = chats[info.id] = { info:info, after:0, feed:feed, inner:inner, tools:{}, perms:{}, live:null, polling:false, born:Date.now() };
      feed.addEventListener('click', onFeedClick);
    }
    c.info = info;
    return c;
  }
  function atBottom(c){ return c.feed.scrollHeight - c.feed.scrollTop - c.feed.clientHeight < 120; }
  function add(c, node){ var stick = atBottom(c); c.inner.appendChild(node); if(stick) c.feed.scrollTop = c.feed.scrollHeight; return node; }

  function render(c, ev){
    var k = ev.kind, n;
    if(k === 'user'){
      n = el('div', 'msg-u');
      var th = (ev.thumbs || []).map(function(u){ return '<img src="' + esc(u) + '" alt="ภาพที่แนบ">'; }).join('');
      var extra = [];
      if(ev.images && !(ev.thumbs || []).length) extra.push('แนบภาพ ' + ev.images + ' ภาพ');
      if((ev.files || []).length) extra.push('ไฟล์ ' + ev.files.map(esc).join(', '));
      n.innerHTML = (th ? '<div class="thumbs">' + th + '</div>' : '') + esc(ev.text) + (extra.length ? '<div class="meta">' + extra.join(' ') + '</div>' : '');
      add(c, n);
    } else if(k === 'live_start'){
      c.live = add(c, el('div', 'msg-a live'));
    } else if(k === 'delta'){
      if(!c.live) c.live = add(c, el('div', 'msg-a live'));
      var stick = atBottom(c);
      c.live.textContent += ev.text;
      if(stick) c.feed.scrollTop = c.feed.scrollHeight;
    } else if(k === 'live_end'){
      if(c.live){ c.live.remove(); c.live = null; }
    } else if(k === 'text'){
      if(c.live && !ev.hist){ c.live.remove(); c.live = null; }
      add(c, el('div', 'msg-a md', md(ev.text)));
    } else if(k === 'tool'){
      var m = toolMeta(ev.name, ev.input);
      n = el('details', 'tool');
      n.innerHTML = '<summary><span class="ti">' + svg(m[1]) + '</span><span class="tn">' + esc(m[0]) + '</span><span class="ts">' + esc(m[2] || '') + '</span><span class="tr ' + (ev.hist ? 'ok' : 'run') + '"></span></summary>'
        + '<div class="tb">' + toolBody(ev.name, ev.input) + '</div><div class="subs"></div>';
      if(ev.name === 'TodoWrite' || ev.name === 'ExitPlanMode') n.open = true;
      c.tools[ev.id] = n;
      add(c, n);
    } else if(k === 'tool_result'){
      var t = c.tools[ev.id];
      if(t){
        var r = t.querySelector('.tr');
        r.className = 'tr ' + (ev.error ? 'err' : 'ok');
        r.innerHTML = ev.error ? svg('<path d="M6 6l12 12M18 6L6 18"/>', 14) : svg('<path d="M20 6 9 17l-5-5"/>', 14);
        if(ev.text) t.querySelector('.tb').insertAdjacentHTML('beforeend', '<div class="lbl">ผลลัพธ์</div><pre>' + esc(ev.text) + '</pre>');
        if(ev.error) t.open = true;
      }
    } else if(k === 'sub'){
      var p = c.tools[ev.parent];
      if(p){ var mm = toolMeta(ev.name, ev.input); p.querySelector('.subs').insertAdjacentHTML('beforeend', '<div>' + esc(mm[0]) + ' ' + esc(mm[2] || '') + '</div>'); }
    } else if(k === 'perm'){
      var pm = toolMeta(ev.tool, ev.input);
      n = el('div', 'ask');
      n.innerHTML = '<div class="ah"><span class="bdg">ขออนุญาต</span>' + esc(pm[0]) + ' <span class="ts dimts">' + esc(pm[2] || '') + '</span></div>'
        + (ev.cut ? '<div class="errbox">คำขอนี้ยาว ' + esc(ev.size) + ' ตัวอักษร แสดงไม่ครบ อย่ากดอนุญาตถ้าไม่แน่ใจ</div>' : '')
        + toolBody(ev.tool, ev.input).replace(/<div class="lbl">[^<]*<\/div>/g, '')
        + '<div class="btns" data-pid="' + esc(ev.pid) + '"><button class="btn pri" data-act="allow">อนุญาต</button>'
        + '<button class="btn sec2" data-act="always">อนุญาต ' + esc(pm[0]) + ' ตลอดเซสชันนี้</button>'
        + '<button class="btn no" data-act="deny">ไม่อนุญาต</button></div>';
      c.perms[ev.pid] = n;
      add(c, n);
    } else if(k === 'ask'){
      n = el('div', 'ask');
      var qs = Array.isArray(ev.questions) ? ev.questions : [];
      n.innerHTML = '<div class="ah"><span class="bdg">Claude ถาม</span></div>' + qs.map(function(q, qi){
        var type = q.multiSelect ? 'checkbox' : 'radio';
        return '<div class="q" data-q="' + qi + '"><div class="qh">' + esc(q.question) + '</div>'
          + (Array.isArray(q.options) ? q.options : []).map(function(o){ o = o || {}; return '<label class="opt"><input type="' + type + '" name="q' + esc(ev.pid) + '_' + qi + '" value="' + esc(o.label) + '"><span>' + esc(o.label) + '<small>' + esc(o.description || '') + '</small></span></label>'; }).join('')
          + '<input type="text" placeholder="หรือพิมพ์คำตอบเอง"></div>';
      }).join('') + '<div class="btns" data-pid="' + esc(ev.pid) + '"><button class="btn pri" data-act="answer">ส่งคำตอบ</button><button class="btn sec2" data-act="skip">ไม่ตอบ</button></div>';
      n._questions = qs;
      c.perms[ev.pid] = n;
      add(c, n);
    } else if(k === 'perm_done'){
      var pc = c.perms[ev.pid];
      if(pc){
        var b = pc.querySelector('.btns'), isQ = !!pc._questions;
        var lbl = isQ ? (ev.allow ? 'ส่งคำตอบแล้ว' : 'ไม่ตอบ') : (ev.allow ? 'อนุญาตแล้ว' : 'ไม่อนุญาต');
        if(b) b.outerHTML = '<div class="done ' + (ev.allow ? 'y' : 'n') + '">' + lbl + '</div>';
      }
    } else if(k === 'result'){
      if(c.live){ c.live.remove(); c.live = null; }
      var txt = ev.ok ? ('เสร็จใน ' + Math.max(1, Math.round((ev.ms || 0) / 1000)) + ' วินาที') : ('จบแบบมีปัญหา ' + esc(ev.text || (ev.errors || []).join(' ') || ev.subtype));
      add(c, el('div', 'foot-line', txt));
    } else if(k === 'error'){
      n = el('div', 'errbox', esc(ev.auth ? 'ยังไม่ได้ล็อกอินบัญชีบริษัท หรือการล็อกอินหมดอายุ' : ev.text));
      if(ev.auth || /login|auth/i.test(ev.text || '')) n.insertAdjacentHTML('beforeend', '<br><button class="btn pri" data-act="login">ล็อกอินบัญชีบริษัท</button>');
      add(c, n);
    } else if(k === 'task'){
      var tl = c.info.tasks = c.info.tasks || [], ix = tl.findIndex(function(t){ return t.id === ev.id; });
      var tv = { id:ev.id, desc:ev.desc, type:ev.type, status:ev.status, tool:ev.tool, summary:ev.summary };
      if(ix >= 0) tl[ix] = tv; else tl.push(tv);
      if(active === c.info.id) renderTasks();
    } else if(k === 'auto_turn'){
      add(c, el('div', 'note', 'งานเบื้องหลังเสร็จ Claude ทำต่อเอง'));
    } else if(k === 'context'){
      c.info.ctx = ev; if(active === c.info.id) loadPanel();
    } else if(k === 'hist_end'){
      add(c, el('div', 'note', 'ประวัติเดิมอยู่ด้านบน พิมพ์ต่อได้เลย'));
    }
  }

  function poll(c){
    if(c.polling) return;
    c.polling = true;
    api('/api/events?id=' + encodeURIComponent(c.info.id) + '&after=' + c.after).then(function(r){
      if(!r.ok){ delete chats[c.info.id]; c.feed.remove(); if(active === c.info.id){ active = null; show(); } return; }
      r.events.forEach(function(ev){
        // one odd event must not stall the feed: show it and move on
        try { render(c, ev); } catch(err){ add(c, el('div', 'errbox', esc('แสดงรายการนี้ไม่ได้ (' + err.message + ')'))); }
        c.after = ev.n + 1;
      });
      c.info = r.chat;
      if(active === c.info.id) header();
    }).catch(function(){ return new Promise(function(res){ setTimeout(res, 1500); }); })
      .then(function(){ c.polling = false; if(chats[c.info.id] && active === c.info.id) poll(c); });
  }

  // ── layout ──
  function header(){
    var c = active && chats[active];
    $('welcome').hidden = !!c;
    $('state').hidden = !c; $('btn-close').hidden = !c; $('btn-tmenu').hidden = !c;
    if($('t-title').querySelector('input.rn')) return;
    renderChips();
    Object.keys(chats).forEach(function(id){ chats[id].feed.hidden = id !== active; });
    if(!c){ $('t-title').textContent = ''; $('t-sub').textContent = ''; return; }
    $('t-title').textContent = c.info.title;
    $('t-sub').textContent = c.info.cwd || '';
    var st = $('state');
    if(c.info.waiting){ st.className = 'state wait'; st.innerHTML = 'รอคุณตอบ'; }
    else if(c.info.busy){ st.className = 'state busy'; st.innerHTML = '<span class="spin sm"></span>กำลังทำงาน'; }
    else { st.className = 'state'; st.textContent = 'พร้อม'; }
    $('btn-stop').hidden = !c.info.busy;
    $('btn-send').disabled = sending || !!c.info.busy;
  }
  function show(id){
    active = id || null;
    header();
    renderOpen();
    var c = active && chats[active];
    if(c){ poll(c); setTimeout(function(){ c.feed.scrollTop = c.feed.scrollHeight; }, 30); }
    if(panelTimer !== null) loadPanel();
    $('input').focus();
  }
  // ── sidebar: one row per session, grouped by project folder, like the Claude app ──
  var acct = null, showAll = {}, folded = {}, histRows = {};
  try { folded = JSON.parse(localStorage.getItem('setto_folded') || '{}') || {}; } catch(err){}
  function pkey(p){ return String(p || '').replace(/[\\/]+$/, '').replace(/\//g, '\\').toLowerCase(); }
  function pname(p){ var parts = String(p || '').split(/[\\/]/).filter(Boolean); return parts.length ? parts[parts.length - 1] : 'ไม่ทราบโฟลเดอร์'; }
  function renderOpen(){ renderSide(); }
  function renderHist(){ renderSide(); }
  function renderSide(){
    if(document.querySelector('#groups input.rn')) return;   // a rename is being typed: don't wipe it
    var q = $('hist-q').value.trim().toLowerCase(), groups = {}, order = [], openSdk = {};
    function group(cwd){
      var k = pkey(cwd);
      if(!groups[k]){ groups[k] = { key:k, cwd:cwd, name:pname(cwd), items:[], ts:0 }; order.push(k); }
      return groups[k];
    }
    Object.keys(chats).forEach(function(id){
      var i = chats[id].info, g = group(i.cwd || '');
      if(i.sdk_id) openSdk[i.sdk_id] = 1;
      g.items.push({ open:id, title:i.title, st:i.waiting ? 'wait' : (i.busy ? 'busy' : ''), ts:Infinity, born:chats[id].born || 0 });
      g.ts = Infinity;
    });
    histRows = {};
    histAll.forEach(function(h){
      if((h.ids || [h.sdk_id]).some(function(x){ return openSdk[x]; })) return;
      histRows[h.sdk_id] = h;
      var g = group(h.cwd || '');
      g.items.push({ resume:h.sdk_id, title:h.title || 'ไม่มีชื่อ', at:h.at, ts:h.ts || 0, born:0 });
      g.ts = Math.max(g.ts, h.ts || 0);
    });
    var list = order.map(function(k){ return groups[k]; }).sort(function(x, y){ return y.ts - x.ts; });
    var html = list.map(function(g){
      var nameHit = q && g.name.toLowerCase().indexOf(q) >= 0;
      var items = g.items.filter(function(it){ return !q || nameHit || it.title.toLowerCase().indexOf(q) >= 0; })
        .sort(function(x, y){ return x.ts === y.ts ? y.born - x.born : (y.ts > x.ts ? 1 : -1); });
      if(q && !items.length) return '';
      var fold = !q && folded[g.key], max = (q || showAll[g.key]) ? items.length : 6;
      var rows = fold ? '' : items.slice(0, max).map(function(it){
        if(it.open) return '<button class="item' + (it.open === active ? ' on' : '') + '" data-open="' + esc(it.open) + '" title="' + esc(it.title) + '"><span class="it">' + esc(it.title) + '</span>' + (it.st ? '<span class="st ' + it.st + '"></span>' : '') + '</button>';
        return '<button class="item" data-resume="' + esc(it.resume) + '" title="' + esc(it.title + (it.at ? '\n' + it.at : '')) + '"><span class="it">' + esc(it.title) + '</span></button>';
      }).join('') + (items.length > max ? '<button class="more" type="button" data-more="' + esc(g.key) + '">ดูเพิ่ม ' + (items.length - max) + '</button>'
        : (showAll[g.key] && items.length > 6 && !q ? '<button class="more" type="button" data-more="' + esc(g.key) + '">ย่อ</button>' : ''));
      return '<div class="grp' + (fold ? ' folded' : '') + '"><div class="gh"><button class="gname" type="button" data-fold="' + esc(g.key) + '" title="' + esc(g.cwd) + '">' + esc(g.name) + '</button>'
        + (g.cwd ? '<button class="gadd" type="button" data-newin="' + esc(g.cwd) + '" title="เซสชันใหม่ใน ' + esc(g.name) + '"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>' : '')
        + '</div>' + rows + '</div>';
    }).join('');
    $('groups').innerHTML = html || '<div class="empty-side">' + (q ? 'ไม่พบ' : 'ยังไม่มีเซสชัน กด New เพื่อเริ่ม') + '</div>';
  }

  // ── session menu (title chevron, or right-click a row): Rename, Fork, Archive, Delete ──
  var menuFor = null;   // {id?, sdk_id?, ids, cwd, title, row?}
  function targetOf(row){
    if(!row){ var c = active && chats[active]; return c ? { id:active, sdk_id:c.info.sdk_id, ids:[], cwd:c.info.cwd, title:c.info.title } : null; }
    var o = row.getAttribute('data-open');
    if(o && chats[o]){ var i = chats[o].info; return { id:o, sdk_id:i.sdk_id, ids:[], cwd:i.cwd, title:i.title, row:row }; }
    var h = histRows[row.getAttribute('data-resume')];
    return h ? { sdk_id:h.sdk_id, ids:h.ids || [h.sdk_id], cwd:h.cwd, title:h.title, row:row } : null;
  }
  function openSessMenu(t, x, y){
    if(!t) return;
    menuFor = t;
    var m = $('menu-sess');
    m.innerHTML = '<button class="mi" type="button" data-sop="rename"><span class="mt"><b>Rename</b></span><span class="rs">R</span></button>'
      + '<button class="mi" type="button" data-sop="fork"' + (t.sdk_id ? '' : ' disabled') + '><span class="mt"><b>Fork</b></span><span class="rs">F</span></button>'
      + '<div class="sep"></div>'
      + '<button class="mi" type="button" data-sop="archive"><span class="mt"><b>Archive</b></span><span class="rs">A</span></button>'
      + '<button class="mi danger" type="button" data-sop="delete"><span class="mt"><b>Delete</b></span><span class="rs">D</span></button>';
    m.hidden = false;
    var w = m.offsetWidth, hgt = m.offsetHeight;
    m.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + 'px';
    m.style.top = Math.max(8, Math.min(y, innerHeight - hgt - 8)) + 'px';
  }
  function closeSessMenu(){ $('menu-sess').hidden = true; menuFor = null; }
  function sessOp(t, op, extra){
    return api('/api/session', Object.assign({ op:op, id:t.id || '', sdk_id:t.sdk_id || '', ids:t.ids || [], cwd:t.cwd || '', title:t.title || '' }, extra || {}));
  }
  function startRename(t){
    var span = t.row ? t.row.querySelector('.it') : $('t-title');
    if(!span) return;
    var inp = el('input', 'rn'); inp.value = t.title || ''; inp.maxLength = 120;
    span.textContent = ''; span.appendChild(inp); inp.focus(); inp.select();
    var done = false;
    function finish(save){
      if(done) return; done = true;
      var v = inp.value.trim();
      inp.remove();
      if(save && v && v !== t.title){
        if(t.id && chats[t.id]) chats[t.id].info.title = v;
        sessOp(t, 'rename', { title:v }).then(function(r){ if(!r.ok) hint(r.error); refreshState(true); });
      }
      header(); renderSide();
    }
    inp.onkeydown = function(e){ e.stopPropagation(); if(e.key === 'Enter'){ e.preventDefault(); finish(true); } if(e.key === 'Escape'){ e.preventDefault(); finish(false); } };
    inp.onclick = function(e){ e.stopPropagation(); };
    inp.onblur = function(){ finish(true); };
  }
  function runSessOp(op, btn){
    var t = menuFor; if(!t) return;
    if(op === 'delete' && btn && !btn.classList.contains('sure')){
      btn.classList.add('sure'); btn.querySelector('b').textContent = 'กดอีกครั้งเพื่อลบถาวร'; return;
    }
    closeSessMenu();
    if(op === 'rename') return startRename(t);
    if(op === 'fork') return sessOp(t, 'fork').then(function(r){ if(!r.ok) return hint(r.error); newChat(r.sdk_id, (t.title || 'เซสชัน') + ' (fork)', r.cwd || t.cwd); });
    sessOp(t, op).then(function(r){
      if(!r.ok) return hint(r.error);
      if(t.id && chats[t.id]){ chats[t.id].feed.remove(); delete chats[t.id]; if(active === t.id) show(null); }
      hint(op === 'delete' ? 'ลบเซสชันแล้ว' : 'เก็บเข้า Archive แล้ว');
      refreshState(true);
    });
  }

  // bottom row: the account signed in on this PC; its menu holds log in / log out
  function initials(a){
    var n = String((a && (a.name || a.email)) || '?').replace(/@.*/, '').trim();
    var w = n.split(/[\s._-]+/).filter(Boolean);
    return ((w[0] || '?').charAt(0) + (w.length > 1 ? w[1].charAt(0) : ((w[0] || '').charAt(1) || ''))).toUpperCase();
  }
  function planName(p){ p = String(p || ''); return p ? p.charAt(0).toUpperCase() + p.slice(1) : ''; }
  function renderAcct(a){
    acct = a || null;
    var box = $('who');
    if(!a || (a.loggedIn == null && !a.noCli)){ box.innerHTML = '<span class="spin sm"></span>'; return; }
    if(a.noCli){ box.innerHTML = '<span class="av off">!</span><span class="an"><b>ยังไม่ได้ติดตั้ง Claude Code</b></span>'; return; }
    if(a.loggedIn){
      box.innerHTML = '<span class="av">' + esc(initials(a)) + '</span><span class="an"><b>' + esc(a.name || a.email || 'ล็อกอินแล้ว') + '</b>'
        + (a.plan ? '<span class="pl"> · ' + esc(planName(a.plan)) + '</span>' : '') + '</span>'
        + '<svg class="chev" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
    } else {
      box.innerHTML = '<span class="av off">?</span><span class="an"><b>ยังไม่ได้ล็อกอิน</b></span><span class="login-pill" data-act="login">ล็อกอิน</span>';
    }
    if(!$('acct-menu').hidden) renderAcctMenu();
  }
  function renderAcctMenu(){
    var a = acct || {}, m = $('acct-menu');
    var folder = '<button class="mi" type="button" data-act="folder"><span class="mt"><b>เปลี่ยนโฟลเดอร์งาน</b></span></button>';
    if(!a.loggedIn){
      m.innerHTML = '<div class="am-h"><b>ยังไม่ได้ล็อกอิน</b><small>ล็อกอินด้วยอีเมลบริษัท</small></div><div class="sep"></div>'
        + '<button class="mi" type="button" data-act="login"><span class="mt"><b>ล็อกอิน</b></span></button>' + folder;
      return;
    }
    m.innerHTML = '<div class="am-h"><b>' + esc(a.email || a.name || '') + '</b><small>' + esc([a.org, planName(a.plan)].filter(Boolean).join(' · ')) + '</small></div><div class="sep"></div>'
      + '<button class="mi" type="button" data-act="usage"><span class="mt"><b>Usage</b></span></button>' + folder
      + '<div class="sep"></div><button class="mi danger" type="button" data-act="logout"><span class="mt"><b>ล็อกเอาท์</b></span></button>';
  }
  function logout(btn){
    if(!btn.classList.contains('sure')){ btn.classList.add('sure'); btn.querySelector('b').textContent = 'กดอีกครั้งเพื่อล็อกเอาท์'; return; }
    btn.disabled = true; btn.querySelector('b').textContent = 'กำลังล็อกเอาท์…';
    api('/api/logout', {}).then(function(r){
      $('acct-menu').hidden = true;
      hint(r.ok ? 'ล็อกเอาท์แล้ว กดมุมซ้ายล่างเพื่อล็อกอินใหม่' : r.error);
      usage = {}; renderUsage(); refreshState(true);
    }).catch(function(e){ hint('ล็อกเอาท์ไม่สำเร็จ ' + e.message); btn.disabled = false; });
  }

  // usage: ring next to the model chip + popover (session = the 5-hour window, weekly)
  function resetText(u){
    if(!u || !u.resets_at) return '';
    var d = new Date(u.resets_at > 1e12 ? u.resets_at : u.resets_at * 1000), mins = Math.round((d - Date.now()) / 60000);
    if(mins <= 0) return 'รีเซ็ตแล้ว';
    if(mins < 24 * 60) return 'รีเซ็ตในอีก ' + (mins >= 60 ? Math.floor(mins / 60) + ' ชม. ' : '') + (mins % 60) + ' นาที';
    return 'รีเซ็ต ' + d.toLocaleString('th-TH', { weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' });
  }
  function renderUsage(){
    var NAMES = { five_hour:'Session limit', seven_day:'Weekly', seven_day_opus:'Weekly (Opus)', seven_day_sonnet:'Weekly (Sonnet)' };
    var rows = ['five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet'].filter(function(k){ return usage && usage[k] && usage[k].util != null; });
    var s = usage && usage.five_hour && usage.five_hour.util != null ? Math.round(usage.five_hour.util) : null;
    var ring = $('btn-usage');
    ring.style.setProperty('--p', (s || 0) + '%');
    ring.className = 'ring' + (s == null ? ' none' : s >= 90 ? ' full' : s >= 70 ? ' hot' : '');
    ring.title = s == null ? 'Usage' : 'Session ' + s + '% used';
    $('menu-usage').innerHTML = '<div class="uh">Usage</div>' + (rows.length ? rows.map(function(k){
      var u = usage[k], pct = Math.max(0, Math.min(100, Math.round(u.util)));
      return '<div class="u-row"><div class="u-top"><b>' + esc(NAMES[k]) + '</b><span>' + pct + '% used</span></div><div class="u-bar"><i class="' + (pct >= 90 ? 'full' : pct >= 70 ? 'hot' : '') + '" data-w="' + pct + '"></i></div><div class="u-at">' + esc(resetText(u)) + '</div></div>';
    }).join('') : '<div class="u-none">ยังไม่มีข้อมูล ล็อกอินแล้วรอสักครู่</div>');
    widths($('menu-usage'));
  }

  // ── mode / model / effort (active session, or the defaults for the next one) ──
  function cur(){ var c = active && chats[active]; return c ? c.info : prefs; }
  function modelLabel(v){ var m = models.filter(function(x){ return x.value === v; })[0]; return m ? m.name.replace(/\s*\(recommended\)/i, '') : (v || 'Default'); }
  function renderChips(){
    var i = cur() || {}, mode = i.mode || 'default', mu = MODES_UI.filter(function(m){ return m[0] === mode; })[0] || MODES_UI[1];
    var cm = $('chip-mode');
    cm.className = 'chip' + (mode === 'bypassPermissions' ? ' bypass' : mode === 'auto' ? ' auto' : '');
    cm.innerHTML = esc(mu[1]) + ' <span class="dim">&#9662;</span>';
    $('chip-model').innerHTML = (i.fast && isOpus(i.model || 'claude-opus-5-5') ? '<span class="bolt" title="Fast mode">&#9889;</span>' : '') + esc(modelLabel(i.model || 'claude-opus-5-5')) + ' <span class="dim">' + esc(EFFORT_TH[i.effort || ''] || '') + ' &#9662;</span>';
  }
  function setOpt(kv){
    var c = active && chats[active];
    Object.keys(kv).forEach(function(k){ (c ? c.info : prefs)[k] = kv[k]; prefs[k] = kv[k]; });
    renderChips();
    if(c) api('/api/opts', Object.assign({ id:active }, kv)).then(function(r){ if(!r.ok) hint(r.error); });
    else api('/api/prefs', kv);
  }
  // model menu laid out like the Claude app: main models with number keys, Effort and More models
  // as side menus, Fast mode switch
  var MAIN_MODELS = [['claude-opus-5-5', 'Opus 5.5'], ['claude-fable-5-1', 'Fable 5.1'], ['claude-sonnet-5-5', 'Sonnet 5.5'], ['claude-haiku-5-5', 'Haiku 5.5']];
  var I_CHECK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  var I_NEXT = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';
  function curModel(){ var j = cur() || {}; return j.model || 'claude-opus-5-5'; }
  function modelInfo(v){ return models.filter(function(x){ return x.value === v; })[0]; }
  function isOpus(v){ return /opus/i.test(v || '') || v === 'default'; }
  function modelMenuHtml(){
    var j = cur() || {}, mv = curModel(), mainIds = MAIN_MODELS.map(function(m){ return m[0]; });
    var info = modelInfo(mv) || {}, efforts = (info.efforts && info.efforts.length) ? info.efforts : ['low', 'medium', 'high', 'xhigh', 'max'];
    var more = models.filter(function(m){ return m.value && mainIds.indexOf(m.value) < 0; });
    var html = MAIN_MODELS.map(function(m, k){
      var on = m[0] === mv;
      return '<button class="mi row" type="button" data-model="' + m[0] + '"><span class="mt"><b>' + esc((modelInfo(m[0]) || {}).name || m[1]) + '</b></span><span class="rs">' + (on ? '<span class="okc">' + I_CHECK + '</span>' : (k + 1)) + '</span></button>';
    }).join('');
    html += '<div class="sep"></div>';
    html += '<div class="mi row sub" tabindex="0"><span class="mt"><b>Effort</b></span><span class="rs">' + esc(EFFORT_TH[j.effort || ''] || '') + ' ' + I_NEXT + '</span><div class="fly">'
      + [''].concat(efforts).map(function(e){ return '<button class="mi row" type="button" data-effort="' + e + '"><span class="mt"><b>' + esc(EFFORT_TH[e] || e) + '</b></span><span class="rs">' + ((j.effort || '') === e ? '<span class="okc">' + I_CHECK + '</span>' : '') + '</span></button>'; }).join('')
      + '</div></div>';
    if(more.length) html += '<div class="mi row sub" tabindex="0"><span class="mt"><b>More models</b></span><span class="rs">' + I_NEXT + '</span><div class="fly">'
      + more.map(function(m){ return '<button class="mi row" type="button" data-model="' + esc(m.value) + '" title="' + esc(m.desc) + '"><span class="mt"><b>' + esc(m.name.replace(/\s*\(recommended\)/i, '')) + '</b>' + (m.desc ? '<small>' + esc(m.desc) + '</small>' : '') + '</span><span class="rs">' + (m.value === mv ? '<span class="okc">' + I_CHECK + '</span>' : '') + '</span></button>'; }).join('')
      + '</div></div>';
    var canFast = isOpus(mv);
    html += '<div class="sep"></div><div class="mh">Fast mode</div>'
      + '<button class="mi row" type="button" data-fast="1"' + (canFast ? '' : ' disabled title="ใช้ได้กับ Opus"') + '><span class="mt"><b>Enable fast mode</b></span><span class="sw' + (j.fast && canFast ? ' on' : '') + '"><i></i></span></button>';
    return html;
  }
  function pickModel(v){
    var kv = { model:v };
    if(!isOpus(v) && (cur() || {}).fast) kv.fast = false;
    setOpt(kv); $('menu-model').innerHTML = modelMenuHtml();
  }
  function openMenu(which){
    var mm = $('menu-mode'), md = $('menu-model');
    if(which === 'mode'){
      var i = cur() || {}, mode = i.mode || 'default';
      mm.innerHTML = '<div class="mh">โหมด</div>' + MODES_UI.map(function(m, k){
        return '<button class="mi" type="button" data-mode="' + m[0] + '"><span class="ck">' + (m[0] === mode ? '&#10003;' : '') + '</span><span class="mt"><b>' + esc(m[1]) + (m[3] ? '<span class="rec">แนะนำ</span>' : '') + '</b><small>' + esc(m[2]) + '</small></span><span class="rs">' + (k + 1) + '</span></button>';
      }).join('');
      mm.hidden = !mm.hidden; md.hidden = true;
    } else {
      md.innerHTML = modelMenuHtml();
      md.hidden = !md.hidden; mm.hidden = true;
    }
  }
  function loadCommands(refresh){
    api('/api/commands' + (refresh ? '?refresh=1' : '')).then(function(r){
      if(!r.ok) return;
      commandList = r.commands || []; models = r.models || [];
      if(!commandList.length || !models.length) setTimeout(function(){ loadCommands(false); }, 4000);
      renderChips();
    }).catch(function(){});
  }

  // ── slash commands ──
  function cmdMatches(){
    var v = $('input').value;
    if(v.charAt(0) !== '/' || /\s/.test(v)) return null;
    var q = v.slice(1).toLowerCase();
    return commandList.filter(function(c){ return c.name.toLowerCase().indexOf(q) === 0; })
      .concat(commandList.filter(function(c){ return q && c.name.toLowerCase().indexOf(q) > 0; })).slice(0, 40);
  }
  function renderCmds(){
    var list = cmdMatches(), box = $('cmds');
    if(!list || !list.length){ box.hidden = true; return; }
    cmdSel = Math.min(cmdSel, list.length - 1);
    box.hidden = false;
    box.innerHTML = list.map(function(c, k){
      return '<button type="button" class="cmd' + (k === cmdSel ? ' on' : '') + '" data-cmd="' + esc(c.name) + '"><b>/' + esc(c.name) + (c.hint ? ' ' + esc(c.hint) : '') + '</b><span>' + esc(c.desc) + '</span></button>';
    }).join('');
    var on = box.querySelector('.cmd.on'); if(on) on.scrollIntoView({ block:'nearest' });
  }
  function pickCmd(name){ $('input').value = '/' + name + ' '; $('cmds').hidden = true; $('input').focus(); autosize(); }

  // ── background tasks (shell commands / agents Claude left running) ──
  var TASK_ST = { running:['wait','กำลังทำ'], pending:['wait','รอ'], paused:['', 'พัก'], completed:['ok','เสร็จ'], failed:['bad','ล้ม'], killed:['bad','หยุดแล้ว'], stopped:['bad','หยุดแล้ว'] };
  function renderTasks(){
    var c = active && chats[active], list = (c && c.info.tasks) || [];
    var running = list.filter(function(t){ return t.status === 'running' || t.status === 'pending'; }).length;
    $('p-task').innerHTML = c ? '<div class="ph">Background tasks<span class="pv">' + (running ? running + ' กำลังทำ' : (list.length ? 'ไม่มีที่กำลังทำ' : '')) + '</span></div>'
      + (list.length ? list.slice().reverse().map(function(t){
          var st = TASK_ST[t.status] || ['', t.status];
          var live = t.status === 'running' || t.status === 'pending';
          return '<div class="row" title="' + esc(t.summary || t.desc) + '"><span class="dot ' + st[0] + '"></span><span class="rt">' + esc(t.desc || t.type || t.id) + (t.tool ? ' <span class="rs">' + esc(t.tool) + '</span>' : '') + '</span>'
            + (live ? '<button class="lnk" type="button" data-stoptask="' + esc(t.id) + '">หยุด</button>' : '<span class="rs">' + esc(st[1]) + '</span>') + '</div>';
        }).join('') : '<div class="empty">ยังไม่มี</div>') : '';
  }

  // ── right panel ──
  var I_FOLDER = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
  var I_BRANCH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-10 7"/></svg>';
  var I_FILE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>';
  var openPc = { ctx:false, mcp:false };
  function panelVisible(){ var a = $('app'); return !a.classList.contains('nopanel') && getComputedStyle($('panel')).display !== 'none'; }
  function loadPanel(){
    clearTimeout(panelTimer);
    if(!panelVisible()){ panelTimer = setTimeout(loadPanel, 3000); return; }
    api('/api/panel' + (active ? '?id=' + encodeURIComponent(active) : '')).then(renderPanel).catch(function(){})
      .then(function(){ panelTimer = setTimeout(loadPanel, 4000); });
  }
  function renderPanel(p){
    if(!p || !p.ok) return;
    $('p-env').innerHTML = '<div class="ph">โฟลเดอร์งาน<button class="lnk" type="button" data-act="folder">เปลี่ยน</button></div>'
      + '<div class="row">' + I_FOLDER + '<span class="rt" title="' + esc(p.cwd) + '"><b>' + esc(p.folder) + '</b> <span class="rs">' + esc(shortPath(p.cwd)) + '</span></span></div>'
      + (p.branch ? '<div class="row">' + I_BRANCH + '<span class="rt">' + esc(p.branch) + '</span></div>' : '');
    var cx = p.ctx;
    $('p-ctx').innerHTML = '<div class="ph" data-pc="ctx">Context<span class="pv">' + (cx ? Math.round(cx.pct) + '% <span class="mini"><i data-w="' + Math.min(100, cx.pct) + '"></i></span>' : 'ยังไม่มีข้อมูล') + '</span></div>'
      + (cx && openPc.ctx ? '<div class="cat"><span>ใช้ไป</span><span>' + cx.total.toLocaleString() + ' / ' + cx.max.toLocaleString() + '</span></div>' + (cx.cats || []).map(function(c){ return '<div class="cat"><span>' + esc(c.name) + '</span><span>' + c.tokens.toLocaleString() + '</span></div>'; }).join('') : '');
    widths($('p-ctx'));
    var mcp = p.mcp || [], okN = mcp.filter(function(m){ return m.status === 'connected'; }).length;
    $('p-mcp').innerHTML = '<div class="ph" data-pc="mcp">Connectors<span class="pv">' + (mcp.some(function(m){ return m.status; }) ? okN + ' connected' : mcp.length + ' ตัว') + '</span></div>'
      + (openPc.mcp ? mcp.map(function(m){ var st = m.status === 'connected' ? 'ok' : (/fail|error/.test(m.status) ? 'bad' : (m.status ? 'wait' : '')); return '<div class="row"><span class="dot ' + st + '"></span><span class="rt">' + esc(m.name) + '</span><span class="rs">' + esc(m.status || '') + '</span></div>'; }).join('') : '');
    renderTasks();
    var outs = (p.outputs || []).slice().reverse();
    $('p-out').innerHTML = active ? '<div class="ph">ไฟล์ที่สร้าง/แก้</div>' + (outs.length ? outs.map(function(f){ return '<div class="row">' + I_FILE + '<button class="file" type="button" data-file="' + esc(f) + '" title="' + esc(f) + '">' + esc(f.split(/[\\/]/).pop()) + '</button></div>'; }).join('') : '<div class="empty">ยังไม่มี</div>') : '';
    var ch = p.changes;
    $('p-chg').innerHTML = ch ? '<div class="ph">Changes<span class="pv"><span class="add">+' + ch.add + '</span> <span class="del">-' + ch.del + '</span></span></div>'
      + '<div class="cat"><span>ไฟล์ที่แก้</span><span>' + ch.files + '</span></div>' + (ch.new ? '<div class="cat"><span>ไฟล์ใหม่ยังไม่ commit</span><span>' + ch.new + '</span></div>' : '') : '';
  }
  function refreshState(force){
    var t0 = Date.now();
    api('/api/state' + (force ? '?force=1&auth=1' : '')).then(function(s){
      document.querySelector('.brand').title = 'Setto Claude v' + s.version;
      s.chats.forEach(function(i){ ensureChat(i); });
      // a chat opened after this request left is not in its answer yet: keep it
      Object.keys(chats).forEach(function(id){ if(chats[id].born < t0 && !s.chats.some(function(i){ return i.id === id; })){ chats[id].feed.remove(); delete chats[id]; if(active === id) active = null; } });
      histAll = s.history || [];
      prefs = s.prefs || prefs; usage = s.usage || {};
      renderAcct(s.auth); renderUsage(); renderSide(); header();
    }).catch(function(){ hint('ติดต่อโปรแกรมไม่ได้ ปิดหน้าต่างแล้วเปิดใหม่จากไฟล์ .bat'); });
  }

  // ── actions ──
  function newChat(resume, title, cwd){
    return api('/api/new', { resume:resume || null, title:title || '', cwd:cwd || '' }).then(function(r){
      if(!r.ok){ hint(r.error); return null; }
      ensureChat({ id:r.id, title:title || 'เซสชันใหม่', busy:false, cwd:cwd || '', mode:prefs.mode || 'default', model:prefs.model || '', effort:prefs.effort || '' });
      if(cwd && !resume){ loadCommands(true); loadPanel(); }
      show(r.id);
      return r.id;
    });
  }
  function send(){
    var text = $('input').value;
    if(sending || (!text.trim() && !atts.length)) return;
    sending = true; header();   // locked from here: a second Enter must not open a second session
    // take the draft out of the box now, so anything typed while this one uploads stays a new draft
    var sent = atts; atts = []; renderAtts();
    $('input').value = ''; autosize();
    var restore = function(msg){
      hint(msg); sending = false; header();
      var cur = $('input').value;
      $('input').value = cur ? text + '\n\n' + cur : text; autosize();
      atts = sent.concat(atts); renderAtts();
    };
    var go = function(id){
      if(!id){ restore('เปิดเซสชันไม่ได้'); return; }
      sending = true; header();
      var images = sent.filter(function(a){ return a.kind === 'image'; }).map(function(a){ return { media_type:a.media_type, data:a.data }; });
      var files = sent.filter(function(a){ return a.kind === 'file'; }).map(function(a){ return { name:a.name, data:a.data }; });
      api('/api/send', { id:id, text:text, images:images, files:files }).then(function(r){
        if(!r.ok){ restore(r.error); return; }
        hint('');
        var c = chats[id]; if(c){ c.info.busy = true; header(); }
      }).catch(function(e){ restore('ส่งไม่สำเร็จ ' + e.message); })
        .then(function(){ sending = false; header(); });
    };
    if(active && chats[active]) go(active);
    else newChat(null, text.trim().split('\n')[0].slice(0, 60)).catch(function(){ return null; }).then(go);
  }
  function answerPerm(btn){
    var box = btn.closest('.btns'), pid = box.getAttribute('data-pid'), act = btn.getAttribute('data-act'), card = btn.closest('.ask');
    var body = { id:active, pid:pid, allow:act === 'allow' || act === 'always' || act === 'answer', always:act === 'always' };
    if(act === 'answer'){
      var answers = {};
      (card._questions || []).forEach(function(q, qi){
        var wrap = card.querySelector('[data-q="' + qi + '"]');
        var free = wrap.querySelector('input[type=text]').value.trim();
        var picked = Array.prototype.map.call(wrap.querySelectorAll('input:checked'), function(x){ return x.value; });
        answers[q.question] = free || (q.multiSelect ? picked : (picked[0] || ''));
      });
      body.answers = answers;
    }
    box.querySelectorAll('button').forEach(function(b){ b.disabled = true; });
    var unlock = function(){ box.querySelectorAll('button').forEach(function(b){ b.disabled = false; }); };
    api('/api/perm', body).then(function(r){ if(!r.ok){ hint(r.error); if(!/หมดอายุ/.test(r.error || '')) unlock(); } })
      .catch(function(e){ hint('ส่งคำตอบไม่สำเร็จ ' + e.message + ' กดใหม่ได้'); unlock(); });
  }
  function onFeedClick(e){
    var b = e.target.closest('button');
    if(b && b.classList.contains('copy')){
      navigator.clipboard.writeText(b.parentNode.querySelector('code').textContent);
      b.textContent = 'ก๊อปแล้ว'; setTimeout(function(){ b.textContent = 'ก๊อป'; }, 1200); return;
    }
    if(b && b.closest('.btns')) return answerPerm(b);
    if(b && b.getAttribute('data-act') === 'login') return login();
    var img = e.target.closest('.msg-u img');
    if(img){ var v = el('div', 'viewer', '<img src="' + esc(img.src) + '" alt="">'); v.onclick = function(){ v.remove(); }; document.body.appendChild(v); }
  }
  function login(){ api('/api/login', {}).then(function(){ hint('หน้าต่างล็อกอินเปิดแล้ว ล็อกอินด้วยอีเมลบริษัท เสร็จแล้วปิดหน้าต่างนั้น'); setTimeout(function(){ refreshState(true); }, 15000); }); }

  // ── attachments ──
  function readFile(f){
    return new Promise(function(res){
      var r = new FileReader();
      r.onload = function(){ res(r.result); };
      r.onerror = function(){ res(null); };
      r.readAsDataURL(f);
    });
  }
  function shrink(dataUrl, type){
    return new Promise(function(res){
      var img = new Image();
      img.onload = function(){
        var max = 1800, w = img.width, h = img.height, big = Math.max(w, h) > max, heavy = dataUrl.length > 1800000;
        if(!big && !heavy){ res({ url:dataUrl, type:type }); return; }
        var s = Math.min(1, max / Math.max(w, h)), cv = document.createElement('canvas');
        cv.width = Math.round(w * s); cv.height = Math.round(h * s);
        cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
        var out = type === 'image/png' && !heavy ? cv.toDataURL('image/png') : cv.toDataURL('image/jpeg', 0.9);
        res({ url:out, type:out.slice(5, out.indexOf(';')) });
      };
      img.onerror = function(){ res({ url:dataUrl, type:type }); };
      img.src = dataUrl;
    });
  }
  function addFiles(list){
    Array.prototype.forEach.call(list, function(f){
      readFile(f).then(function(url){
        if(!url) return;
        if(/^image\/(png|jpeg|gif|webp)$/.test(f.type)){
          shrink(url, f.type).then(function(s){
            atts.push({ kind:'image', name:f.name || 'ภาพ', media_type:s.type, data:s.url.split(',')[1], url:s.url });
            renderAtts();
          });
        } else {
          if(f.size > 30e6){ hint('ไฟล์ ' + f.name + ' ใหญ่เกิน 30 MB'); return; }
          atts.push({ kind:'file', name:f.name, data:url.split(',')[1] });
          renderAtts();
        }
      });
    });
  }
  function renderAtts(){
    $('atts').hidden = !atts.length;
    $('atts').innerHTML = atts.map(function(a, i){
      var ext = (a.name.split('.').pop() || '').slice(0, 4).toUpperCase();
      return '<div class="att">' + (a.kind === 'image' ? '<img src="' + esc(a.url) + '" alt="">' : '<span class="fi">' + esc(ext) + '</span>')
        + '<span>' + esc(a.name) + '</span><button type="button" data-rm="' + i + '" title="เอาออก">&times;</button></div>';
    }).join('');
  }
  function autosize(){ var t = $('input'); t.style.height = 'auto'; t.style.height = Math.min(260, t.scrollHeight) + 'px'; }

  // ── events ──
  $('btn-new').onclick = function(){ newChat(); };
  $('btn-send').onclick = send;
  $('btn-stop').onclick = function(){ if(active) api('/api/stop', { id:active }); };
  $('btn-close').onclick = function(){
    var c = active && chats[active];
    if(!c) return;
    if(c.info.busy && !confirm('เซสชันนี้กำลังทำงาน ปิดแล้วงานจะหยุด ปิดเลยไหม')) return;
    var id = active;
    api('/api/close', { id:id }).then(function(){ c.feed.remove(); delete chats[id]; if(active === id) show(Object.keys(chats)[0]); refreshState(true); });
  };
  $('chip-mode').onclick = function(e){ e.stopPropagation(); openMenu('mode'); };
  $('chip-model').onclick = function(e){ e.stopPropagation(); openMenu('model'); };
  $('menu-mode').onclick = function(e){ var b = e.target.closest('[data-mode]'); if(b){ setOpt({ mode:b.getAttribute('data-mode') }); $('menu-mode').hidden = true; } e.stopPropagation(); };
  $('menu-model').onclick = function(e){
    e.stopPropagation();
    var m = e.target.closest('[data-model]'); if(m){ pickModel(m.getAttribute('data-model') === 'default' ? '' : m.getAttribute('data-model')); return; }
    var f = e.target.closest('[data-effort]'); if(f){ setOpt({ effort:f.getAttribute('data-effort') }); $('menu-model').innerHTML = modelMenuHtml(); return; }
    var x = e.target.closest('[data-fast]'); if(x && !x.disabled){ setOpt({ fast:!(cur() || {}).fast }); $('menu-model').innerHTML = modelMenuHtml(); }
  };
  $('cmds').onclick = function(e){ var b = e.target.closest('[data-cmd]'); if(b) pickCmd(b.getAttribute('data-cmd')); };
  $('btn-panel').onclick = function(){
    var a = $('app'), wide = window.innerWidth > 1100;
    if(wide) a.classList.toggle('nopanel'); else a.classList.toggle('forcepanel');
    try { localStorage.setItem('setto_panel', a.classList.contains('nopanel') ? '0' : '1'); } catch(err){}
    loadPanel();
  };
  $('panel').onclick = function(e){
    var h = e.target.closest('[data-pc]'); if(h){ openPc[h.getAttribute('data-pc')] = !openPc[h.getAttribute('data-pc')]; loadPanel(); return; }
    var st = e.target.closest('[data-stoptask]'); if(st && active){ api('/api/stoptask', { id:active, task:st.getAttribute('data-stoptask') }); st.disabled = true; return; }
    var f = e.target.closest('[data-file]'); if(f && active){ api('/api/openfile', { id:active, path:f.getAttribute('data-file') }).then(function(r){ if(!r.ok) hint(r.error); }); return; }
    if(e.target.closest('[data-act=folder]')){
      hint('เลือกโฟลเดอร์ในหน้าต่างที่เปิดขึ้น');
      api('/api/folder', {}).then(function(r){ hint(r.ok ? (r.same ? '' : 'เปลี่ยนโฟลเดอร์แล้ว เซสชันใหม่จะทำงานที่นี่') : r.error); refreshState(true); loadCommands(true); loadPanel(); });
    }
  };
  document.addEventListener('click', function(e){
    $('menu-mode').hidden = true; $('menu-model').hidden = true;
    if(!e.target.closest('#dd-usage')) $('menu-usage').hidden = true;
    if(!e.target.closest('#acct')) $('acct-menu').hidden = true;
    if(!e.target.closest('#menu-sess') && !e.target.closest('#btn-tmenu')) closeSessMenu();
  });
  $('btn-usage').onclick = function(){
    var m = $('menu-usage'); renderUsage(); m.hidden = !m.hidden;
    if(!m.hidden) api('/api/state?force=1').then(function(st){ usage = st.usage || usage; setTimeout(function(){ api('/api/state').then(function(s2){ usage = s2.usage || usage; renderUsage(); }); }, 1500); }).catch(function(){});
  };
  $('who').onclick = function(e){
    if(e.target.closest('[data-act=login]')){ $('acct-menu').hidden = true; return login(); }
    var m = $('acct-menu'); renderAcctMenu(); m.hidden = !m.hidden;
  };
  $('acct-menu').onclick = function(e){
    var b = e.target.closest('[data-act]'); if(!b) return;
    var act = b.getAttribute('data-act');
    if(act === 'logout') return logout(b);
    $('acct-menu').hidden = true;
    if(act === 'login') return login();
    if(act === 'usage'){ e.stopPropagation(); renderUsage(); $('menu-usage').hidden = false; }
  };
  $('btn-tmenu').onclick = function(){
    if(!$('menu-sess').hidden){ closeSessMenu(); return; }
    var r = $('btn-tmenu').getBoundingClientRect(); openSessMenu(targetOf(null), r.left - 8, r.bottom + 6);
  };
  $('t-title').ondblclick = function(){ var t = targetOf(null); if(t) startRename(t); };
  $('menu-sess').onclick = function(e){ var b = e.target.closest('[data-sop]'); if(b && !b.disabled) runSessOp(b.getAttribute('data-sop'), b); };
  $('groups').oncontextmenu = function(e){
    var row = e.target.closest('.item'); if(!row) return;
    e.preventDefault(); openSessMenu(targetOf(row), e.clientX, e.clientY);
  };
  $('groups').onclick = function(e){
    if(e.target.closest('input.rn')) return;
    var f = e.target.closest('[data-fold]');
    if(f){ var k = f.getAttribute('data-fold'); folded[k] = !folded[k]; if(!folded[k]) delete folded[k]; try { localStorage.setItem('setto_folded', JSON.stringify(folded)); } catch(err){} return renderSide(); }
    var m = e.target.closest('[data-more]'); if(m){ var mk = m.getAttribute('data-more'); showAll[mk] = !showAll[mk]; return renderSide(); }
    var n = e.target.closest('[data-newin]'); if(n) return newChat(null, '', n.getAttribute('data-newin'));
  };
  $('btn-attach').onclick = function(){ $('file').click(); };
  $('file').onchange = function(e){ addFiles(e.target.files); e.target.value = ''; };
  $('atts').onclick = function(e){ var b = e.target.closest('[data-rm]'); if(b){ atts.splice(+b.getAttribute('data-rm'), 1); renderAtts(); } };
  $('hist-q').oninput = renderHist;
  $('input').addEventListener('input', function(){ autosize(); cmdSel = 0; renderCmds(); });
  $('input').addEventListener('keydown', function(e){
    if(!$('cmds').hidden){
      var list = cmdMatches() || [];
      if(e.key === 'ArrowDown'){ e.preventDefault(); cmdSel = (cmdSel + 1) % list.length; renderCmds(); return; }
      if(e.key === 'ArrowUp'){ e.preventDefault(); cmdSel = (cmdSel - 1 + list.length) % list.length; renderCmds(); return; }
      if((e.key === 'Enter' || e.key === 'Tab') && list[cmdSel] && !e.shiftKey){ e.preventDefault(); pickCmd(list[cmdSel].name); return; }
      if(e.key === 'Escape'){ $('cmds').hidden = true; return; }
    }
    if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); }
  });
  document.addEventListener('paste', function(e){
    var files = Array.prototype.filter.call((e.clipboardData || {}).items || [], function(it){ return it.kind === 'file'; }).map(function(it){ return it.getAsFile(); }).filter(Boolean);
    if(files.length){ e.preventDefault(); addFiles(files); }
  });
  document.addEventListener('keydown', function(e){
    if((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n'){ e.preventDefault(); newChat(); }
    if((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k'){ e.preventDefault(); $('hist-q').focus(); $('hist-q').select(); }
    if(!$('menu-model').hidden && /^[1-4]$/.test(e.key) && document.activeElement !== $('input')){ e.preventDefault(); pickModel(MAIN_MODELS[+e.key - 1][0]); return; }
    if(!$('menu-sess').hidden && !e.ctrlKey && !e.altKey){
      var sk = { r:'rename', f:'fork', a:'archive', d:'delete' }[e.key.toLowerCase()];
      if(sk){ e.preventDefault(); var sb = $('menu-sess').querySelector('[data-sop=' + sk + ']'); if(sb && !sb.disabled) runSessOp(sk, sb); return; }
      if(e.key === 'Escape'){ closeSessMenu(); return; }
    }
    if(e.key === 'Escape' && active && chats[active] && chats[active].info.busy) api('/api/stop', { id:active });
  });
  document.addEventListener('click', function(e){
    var o = e.target.closest('[data-open]'); if(o) return show(o.getAttribute('data-open'));
    var r = e.target.closest('[data-resume]');
    if(r && !e.target.closest('input.rn')){ var h = histRows[r.getAttribute('data-resume')] || {}; return newChat(r.getAttribute('data-resume'), h.title, h.cwd); }
  });
  var dragN = 0;
  document.addEventListener('dragenter', function(e){ if(e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0){ dragN++; $('drop').hidden = false; } });
  document.addEventListener('dragleave', function(){ dragN = Math.max(0, dragN - 1); if(!dragN) $('drop').hidden = true; });
  document.addEventListener('dragover', function(e){ e.preventDefault(); });
  document.addEventListener('drop', function(e){ e.preventDefault(); dragN = 0; $('drop').hidden = true; if(e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });

  try { if(localStorage.getItem('setto_panel') === '0') $('app').classList.add('nopanel'); } catch(err){}
  refreshState(true);
  loadCommands(false);
  setInterval(function(){ refreshState(false); }, 3000);
  show(null);
  loadPanel();
})();
