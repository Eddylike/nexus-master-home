/* NEXUS Skill- & Prompt-Bibliothek UI */
(function () {
  'use strict';
  var state = { category: '', tag: '', items: [] };

  function byId(id) { return document.getElementById(id); }

  function status(text) {
    var node = byId('libraryStatus');
    if (node) node.textContent = text;
  }

  function openFiles(path) {
    var field = byId('filesPath');
    if (field) field.value = path;
    var nav = document.querySelector('#sideNav [data-view="files"]');
    if (nav) nav.click();
    var refresh = byId('btnFilesRefresh');
    if (refresh) refresh.click();
  }

  function promptText(item) {
    if (!item) return '';
    if (item.typ === 'skill' && Array.isArray(item.pipeline)) {
      return item.pipeline.map(function (step, index) {
        return 'Schritt ' + (index + 1) + (step.name ? ' - ' + step.name : '') + ':\n' + (step.prompt || '');
      }).join('\n\n');
    }
    return item.prompt || '';
  }

  function insertIntoChat(item) {
    var text = promptText(item);
    if (!text) { status('Kein Prompttext vorhanden.'); return; }
    var input = byId('nxInput');
    if (!input) { status('Chat-Eingabe nicht gefunden.'); return; }
    var chat = document.querySelector('#sideNav button[data-view="chat"]');
    if (chat) chat.click();
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }

  function copyPrompt(item) {
    var text = promptText(item);
    if (!text) { status('Kein Prompttext vorhanden.'); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        status('Prompt in die Zwischenablage kopiert.');
      }, function () {
        status('Kopieren im Browser nicht möglich.');
      });
    } else {
      status('Kopieren im Browser nicht verfügbar.');
    }
  }

  function matches(item) {
    if (state.category && String(item.kategorie || '').toLowerCase() !== state.category) return false;
    if (state.tag) {
      var wanted = state.tag.replace(/^#/, '').toLowerCase();
      var tags = (item.tags || []).map(function (tag) { return String(tag).toLowerCase(); });
      if (tags.indexOf(wanted) === -1) return false;
    }
    return true;
  }

  function render() {
    var list = byId('libraryList');
    if (!list) return;
    list.replaceChildren();
    var items = state.items.filter(matches);
    status('Quelle: skills/ · Starterbibliothek v1.0 · ' + items.length + '/' + state.items.length + ' Einträge'
      + (state.category ? ' · Kategorie: ' + state.category : '')
      + (state.tag ? ' · Tag: #' + state.tag.replace(/^#/, '') : ''));
    if (!items.length) { list.textContent = 'Keine passenden Einträge.'; return; }

    items.forEach(function (item) {
      var card = document.createElement('div');
      card.className = 'library-item';

      var head = document.createElement('div');
      head.className = 'library-item-head';
      var title = document.createElement('strong');
      title.textContent = item.titel || item.id;
      var badge = document.createElement('span');
      badge.className = 'library-badge';
      badge.textContent = (item.typ === 'skill' ? 'Skill' : 'Prompt') + ' · ' + item.kategorie + ' · v' + (item.version || '?');
      head.append(title, badge);

      var desc = document.createElement('p');
      desc.textContent = item.beschreibung || '';

      var tags = document.createElement('div');
      tags.className = 'library-tags';
      (item.tags || []).forEach(function (tag) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'library-tag';
        chip.textContent = '#' + tag;
        chip.addEventListener('click', function () {
          state.tag = String(tag);
          var field = byId('libraryTag');
          if (field) field.value = tag;
          render();
        });
        tags.append(chip);
      });

      var actions = document.createElement('div');
      actions.className = 'streams-actions';
      var insert = document.createElement('button');
      insert.type = 'button';
      insert.className = 'primary';
      insert.textContent = 'In Chat übernehmen';
      insert.addEventListener('click', function () { insertIntoChat(item); });
      var copy = document.createElement('button');
      copy.type = 'button';
      copy.textContent = 'Kopieren';
      copy.addEventListener('click', function () { copyPrompt(item); });
      actions.append(insert, copy);

      var details = document.createElement('details');
      var summary = document.createElement('summary');
      summary.textContent = item.typ === 'skill'
        ? 'Pipeline anzeigen (' + (item.pipeline || []).length + ' Schritte)'
        : 'Prompt anzeigen';
      var pre = document.createElement('pre');
      pre.className = 'library-prompt';
      pre.textContent = promptText(item);
      details.append(summary, pre);

      card.append(head, desc, tags, actions, details);
      list.append(card);
    });
  }

  function refresh() {
    var list = byId('libraryList');
    if (list) list.textContent = 'Bibliothek wird geladen …';
    return fetch('/api/library', { headers: { Accept: 'application/json' } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data || data.success !== true) throw new Error((data && data.error) || 'Bibliothek nicht verfügbar');
        state.items = data.items || [];
        render();
        return data;
      })
      .catch(function (error) {
        var message = 'Bibliothek nicht verfügbar: ' + (error && error.message ? error.message : error);
        status(message);
        if (list) list.textContent = message;
      });
  }

  function setCategory(category) {
    state.category = String(category || '').toLowerCase();
    document.querySelectorAll('#libraryCategories [data-library-category]').forEach(function (button) {
      button.classList.toggle('active', String(button.getAttribute('data-library-category') || '').toLowerCase() === state.category);
    });
    render();
  }

  function ensureAllButton() {
    var grid = byId('libraryCategories');
    if (!grid || grid.querySelector('[data-library-category=""]')) return;
    var all = document.createElement('button');
    all.type = 'button';
    all.setAttribute('data-library-category', '');
    all.textContent = 'Alle';
    grid.insertBefore(all, grid.firstChild);
  }

  ensureAllButton();

  var refreshButton = byId('btnLibraryRefresh');
  if (refreshButton) refreshButton.addEventListener('click', refresh);

  var tagField = byId('libraryTag');
  if (tagField) {
    tagField.addEventListener('input', function () { state.tag = tagField.value.trim(); render(); });
    tagField.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); state.tag = tagField.value.trim(); render(); }
    });
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var category = target.closest('[data-library-category]');
    if (category) {
      event.preventDefault();
      setCategory(category.getAttribute('data-library-category'));
      return;
    }
    if (target.closest('#btnLibraryOpenRoot')) {
      openFiles('skills');
    }
  });

  window.nxLibraryRefresh = refresh;
})();
