/* Exam runner: one ticket at a time, autosave to the server, 30-min timer.
   Answers sit under the ticket image so long text does not cover the scene. */
(function () {
  const root = document.getElementById('runner');
  if (!root) return;

  const QUESTIONS = JSON.parse(document.getElementById('questions-data').textContent);
  const attemptId = root.dataset.attemptId;
  const mode = root.dataset.mode;            // 'exam' | 'practice'
  const limitSeconds = parseInt(root.dataset.limit, 10) * 60;
  const maxErrors = parseInt(root.dataset.maxErrors, 10);

  const el = {
    dots: document.getElementById('dots'),
    index: document.getElementById('q-index'),
    prompt: document.getElementById('q-prompt'),
    ticket: document.getElementById('ticket'),
    explain: document.getElementById('explain'),
    timer: document.getElementById('timer'),
    prev: document.getElementById('prev'),
    next: document.getElementById('next'),
    finish: document.getElementById('finish'),
    answered: document.getElementById('answered-count'),
    correct: document.getElementById('correct-count'),
    errors: document.getElementById('error-count'),
  };

  function csrfHeaders() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const token = meta && meta.getAttribute('content');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-CSRFToken'] = token;
    return headers;
  }

  let current = 0;
  let started = Date.now();
  let finished = false;

  // answer count -> cover height class, matching the site's own mapping
  const CUTOFF_BY_COUNT = { 2: 'cutoff-1', 3: 'cutoff-2', 4: 'cutoff-3' };

  function answeredCount() {
    return QUESTIONS.filter((q) => q.chosen !== null && q.chosen !== undefined).length;
  }
  function errorCount() {
    return QUESTIONS.filter(
      (q) => q.chosen !== null && q.chosen !== undefined && q.correct !== null && q.chosen !== q.correct
    ).length;
  }
  function correctCount() {
    return QUESTIONS.filter(
      (q) => q.chosen !== null && q.chosen !== undefined && q.correct !== null && q.chosen === q.correct
    ).length;
  }

  function buildDots() {
    el.dots.innerHTML = '';
    QUESTIONS.forEach((q, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'dot';
      b.textContent = i + 1;
      b.setAttribute('aria-label', 'კითხვა ' + (i + 1));
      b.addEventListener('click', () => go(i));
      el.dots.appendChild(b);
    });
  }

  function paintDots() {
    [...el.dots.children].forEach((b, i) => {
      const q = QUESTIONS[i];
      b.className = 'dot';
      const answered = q.chosen !== null && q.chosen !== undefined;
      if (answered) b.classList.add('answered');
      if (answered && q.correct !== null && q.correct !== undefined) {
        b.classList.add(q.chosen === q.correct ? 'correct' : 'wrong');
      }
      if (i === current) b.classList.add('current');
    });
    el.answered.textContent = answeredCount() + ' / ' + QUESTIONS.length;
    if (el.correct) el.correct.textContent = 'სწორი: ' + correctCount();
    if (el.errors) {
      const errs = errorCount();
      el.errors.textContent = 'შეცდომა: ' + errs + ' / ' + maxErrors;
      el.errors.classList.toggle('over', errs > maxErrors);
    }
  }

  /* Layout classes: prefer the ones scraped from the site, fall back to the
     answer count so demo/hand-made tickets still render. */
  function layoutClasses(q) {
    const layout = q.layout || '';
    const count = Math.min(Math.max(q.answers.length, 2), 4);
    const cutoff = (layout.match(/cutoff-[123]/) || [])[0] || CUTOFF_BY_COUNT[count];
    const slots = (layout.match(/answers-num-([234])/) || [])[1] || String(count);
    const classes = [cutoff, 'ans-' + slots];
    if (/big-answers/.test(layout) || count > 2) classes.push('big-answers');
    return classes;
  }

  function answerSlot(q, i, locked) {
    const text = q.answers[i];
    const empty = text === undefined;
    const node = document.createElement(empty ? 'div' : 'button');
    node.className = 'tk-a a' + (i + 1) + (empty ? ' empty' : '');
    if (!empty) node.type = 'button';

    const num = document.createElement('span');
    num.className = 'tk-n';
    const numInner = document.createElement('span');
    numInner.textContent = i + 1;
    num.appendChild(numInner);

    const label = document.createElement('span');
    label.className = 'tk-t';
    label.textContent = empty ? '' : text;

    node.appendChild(num);
    node.appendChild(label);
    if (empty) return node;

    if (q.chosen === i) node.classList.add('selected');
    if (locked) {
      node.classList.add('locked');
      if (i === q.correct) {
        node.classList.remove('selected');
        node.classList.add('correct');
        numInner.textContent = '✓';
      } else if (q.chosen === i) {
        node.classList.remove('selected');
        node.classList.add('wrong');
        numInner.textContent = '✗';
      }
      node.disabled = true;
    }
    node.addEventListener('click', () => choose(i));
    return node;
  }

  function buildTicket(q, locked) {
    const tk = document.createElement('div');
    tk.className = ['tk'].concat(layoutClasses(q)).join(' ');
    if (!q.image) tk.classList.add('no-image');
    if (!locked) tk.classList.add('live');

    if (q.image) {
      const img = document.createElement('img');
      img.className = 'tk-img';
      img.src = q.image;
      img.alt = 'ბილეთი #' + q.id;
      tk.appendChild(img);
    }

    const cover = document.createElement('div');
    cover.className = 'tk-cover';
    const slots = q.answers.length <= 2 ? 2 : 4;
    for (let i = 0; i < slots; i++) cover.appendChild(answerSlot(q, i, locked));
    tk.appendChild(cover);
    return tk;
  }

  function render() {
    const q = QUESTIONS[current];
    const locked = q.chosen !== null && q.chosen !== undefined;
    el.index.textContent = 'კითხვა ' + (current + 1) + ' / ' + QUESTIONS.length + ' · ბილეთი #' + q.id;
    if (el.prompt) el.prompt.textContent = q.question;

    el.ticket.innerHTML = '';
    el.ticket.appendChild(buildTicket(q, locked));

    const showExplain = mode === 'practice' && locked && q.explanation;
    el.explain.hidden = !showExplain;
    if (showExplain) {
      el.explain.innerHTML = '';
      const b = document.createElement('b');
      b.textContent = 'განმარტება: ';
      el.explain.appendChild(b);
      el.explain.appendChild(document.createTextNode(q.explanation));
    }

    el.prev.disabled = current === 0;
    el.next.disabled = current === QUESTIONS.length - 1;
    paintDots();
  }

  function go(i) {
    current = Math.min(Math.max(i, 0), QUESTIONS.length - 1);
    render();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function choose(i) {
    const q = QUESTIONS[current];
    if (q.chosen !== null && q.chosen !== undefined) return;
    q.chosen = i;
    render();

    fetch('/api/attempt/' + attemptId + '/answer', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ ticket_id: q.id, chosen_index: i }),
    }).catch(() => {});
  }

  function finish(auto) {
    if (finished) return;
    const left = QUESTIONS.length - answeredCount();
    if (!auto && left > 0 && !confirm(left + ' კითხვა უპასუხოდ დარჩა. მაინც დაასრულო?')) return;
    finished = true;
    el.finish.disabled = true;
    fetch('/api/attempt/' + attemptId + '/finish', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ seconds: Math.round((Date.now() - started) / 1000) }),
    })
      .then((r) => r.json())
      .then((data) => { window.location.href = data.redirect; })
      .catch(() => { window.location.href = '/result/' + attemptId; });
  }

  function tick() {
    const spent = Math.round((Date.now() - started) / 1000);
    const left = Math.max(limitSeconds - spent, 0);
    const m = String(Math.floor(left / 60)).padStart(2, '0');
    const s = String(left % 60).padStart(2, '0');
    el.timer.textContent = m + ':' + s;
    el.timer.classList.toggle('low', left <= 300);
    if (left === 0) finish(true);
  }

  el.prev.addEventListener('click', () => go(current - 1));
  el.next.addEventListener('click', () => go(current + 1));
  el.finish.addEventListener('click', () => finish(false));

  document.addEventListener('keydown', (e) => {
    if (e.key >= '1' && e.key <= '9') {
      const i = parseInt(e.key, 10) - 1;
      if (i < QUESTIONS[current].answers.length) choose(i);
    }
    if (e.key === 'ArrowLeft') go(current - 1);
    if (e.key === 'ArrowRight') go(current + 1);
  });

  buildDots();
  render();
  tick();
  setInterval(tick, 1000);
})();
