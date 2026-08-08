"""Browser-side English/Polish localization shared by the local UI pages.

The language lives in localStorage instead of config.json.  It therefore
survives app restarts and account resets without becoming part of the user's
booking or session details.
"""

LOCALIZATION_SCRIPT = r"""
<script>
(() => {
  const KEY = 'info-kierowca-notifier-language';
  const PL = {
    'Settings': 'Ustawienia', 'Set up info-kierowca notifier': 'Skonfiguruj info-kierowca notifier',
    'This runs entirely on your machine — nothing but info-kierowca.pl ever sees your PKK number or session.': 'Program działa wyłącznie na Twoim komputerze — numeru PKK ani sesji nie zobaczy nikt poza info-kierowca.pl.',
    'Exam & centers': 'Egzamin i ośrodki', 'Your PKK profile': 'Twój profil PKK', 'Enter manually instead': 'Wprowadź ręcznie',
    'PKK number': 'Numer PKK', 'License category': 'Kategoria prawa jazdy', 'More categories': 'Więcej kategorii',
    'Fewer categories': 'Mniej kategorii', 'Use my PKK profile instead': 'Użyj mojego profilu PKK',
    'Exam type': 'Rodzaj egzaminu', 'Theoretical': 'Teoretyczny', 'Practical': 'Praktyczny',
    'Alerts': 'Powiadomienia', 'Automation': 'Automatyzacja', 'WORD centers to watch (__CENTER_COUNT__ nationwide)': 'Ośrodki WORD do obserwowania (__CENTER_COUNT__ w kraju)',
    'Click to browse all centers, or type to filter...': 'Kliknij, aby przeglądać ośrodki, lub wpisz tekst, aby filtrować...',
    'Date of your current booked slot — a found slot on an earlier date beats this and triggers the alerts below': 'Data obecnie zarezerwowanego terminu — wolny termin wcześniejszy niż ten uruchomi poniższe powiadomienia',
    'Select a date': 'Wybierz datę', 'Preferred time of day': 'Preferowana pora dnia', 'All day': 'Cały dzień',
    "A slot outside this window won't trigger an alert or open the reschedule browser. Checking and the dashboard still show everything found.": 'Termin poza tym przedziałem nie uruchomi powiadomienia ani przeglądarki zmiany terminu. Sprawdzanie i panel nadal pokażą wszystkie znalezione terminy.',
    'Send a phone alert when a slot beats your booked date': 'Wyślij powiadomienie na telefon, gdy termin jest wcześniejszy',
    'Buzzes your phone when a watched center opens a slot on or before your booked date. Turn off to just watch the dashboard.': 'Powiadamia telefon, gdy obserwowany ośrodek udostępni termin w dniu obecnej rezerwacji lub wcześniej. Wyłącz, aby tylko obserwować panel.',
    'Send a phone alert when your session expires': 'Wyślij powiadomienie na telefon po wygaśnięciu sesji',
    'Buzzes your phone when your login expires and Chrome reopens for you to scan the QR again. Turn off to only get the desktop popup.': 'Powiadamia telefon, gdy logowanie wygaśnie i Chrome otworzy się ponownie, aby zeskanować kod QR. Wyłącz, aby otrzymywać tylko komunikat na komputerze.',
    'Your private notification link — install the ': 'Twój prywatny link powiadomień — zainstaluj aplikację ', ' and subscribe to it exactly:': ' i zasubskrybuj dokładnie ten link:',
    'Copy link': 'Kopiuj link', "Anyone who knows this link can read your notifications — don't share it.": 'Każdy, kto zna ten link, może odczytać powiadomienia — nie udostępniaj go.',
    'Send test push': 'Wyślij testowe powiadomienie', 'Check frequency': 'Częstotliwość sprawdzania',
    "Checks land a little later than this at random each time (up to +15%), so requests don't all hit the site on one exact, predictable cadence. Faster than a minute means noticeably more requests against a site with no documented rate limits.": 'Sprawdzenia odbywają się za każdym razem losowo nieco później (do +15%), aby żądania nie trafiały na stronę w identycznym rytmie. Częściej niż raz na minutę oznacza wyraźnie więcej żądań do strony bez udokumentowanych limitów.',
    'Reopen Chrome to log back in': 'Otwórz ponownie Chrome, aby się zalogować', 'When your session expires, relaunch Chrome at the login screen so you can scan the QR again.': 'Gdy sesja wygaśnie, ponownie otwiera Chrome na ekranie logowania, aby można było znów zeskanować kod QR.',
    'Open my booking when a slot beats your booked date': 'Otwórz moją rezerwację, gdy znajdzie się wcześniejszy termin', 'Opens a logged-in browser at your booking\'s "change date" screen. You still pick the date and confirm yourself.': 'Otwiera zalogowaną przeglądarkę na ekranie zmiany terminu rezerwacji. Datę wybierasz i potwierdzasz samodzielnie.',
    'Experimental: auto-select the matching slot': 'Eksperymentalne: automatycznie wybierz pasujący termin', 'Unverified against the live site. Also expands the matching date group and picks the exact exam type/time on the change-date screen, then goes to the summary/review screen. Still stops there — nothing is submitted.': 'Niezweryfikowane na działającej stronie. Rozwija także grupę pasującej daty, wybiera dokładny rodzaj/godzinę egzaminu i przechodzi do ekranu podsumowania. Na tym kończy — nic nie jest wysyłane.',
    'Experimental: auto-confirm the reservation change': 'Eksperymentalne: automatycznie potwierdź zmianę rezerwacji', 'Requires the toggle above. Submits the actual date change with no review step — the one action in this project that can\'t be undone by closing the browser. Only turn this on once you\'ve watched auto-select pick the right slot reliably.': 'Wymaga powyższego przełącznika. Wysyła faktyczną zmianę terminu bez kroku sprawdzania — jedyne działanie w programie, którego nie można cofnąć przez zamknięcie przeglądarki. Włącz je dopiero, gdy automatyczny wybór niezawodnie wskazuje właściwy termin.',
    'Save and log in': 'Zapisz i zaloguj', 'Save changes': 'Zapisz zmiany', 'Reset account': 'Zresetuj konto', "Logs you out and clears your saved settings — you'll land back on the QR login screen.": 'Wylogowuje i usuwa zapisane ustawienia — wrócisz do ekranu logowania kodem QR.',
    'Back to dashboard': 'Wróć do panelu', 'Show or hide PKK number': 'Pokaż lub ukryj numer PKK', 'Show or hide notification link': 'Pokaż lub ukryj link powiadomień',
    'No centers yet — search above to add one.': 'Nie wybrano jeszcze ośrodków — wyszukaj powyżej, aby dodać.', 'Remove': 'Usuń', 'No matching centers.': 'Brak pasujących ośrodków.', 'All centers added.': 'Dodano wszystkie ośrodki.',
    'Connect your account': 'Połącz konto', 'Log in with mObywatel': 'Zaloguj się przez mObywatel', 'Waiting for QR scan...': 'Oczekiwanie na skan kodu QR...', 'Skip and enter my PKK number manually': 'Pomiń i wpisz numer PKK ręcznie',
    "Log in once via the mObywatel QR code — this is what lets the notifier check for\n  slots on your behalf. While we're at it, we'll also find your PKK number and license category\n  automatically, so you don't have to type them in.": 'Zaloguj się raz kodem QR w aplikacji mObywatel — dzięki temu program może sprawdzać terminy w Twoim imieniu. Przy okazji automatycznie znajdzie numer PKK i kategorię prawa jazdy, więc nie trzeba ich wpisywać ręcznie.',
    "A Chrome window should open — scan the QR code in the mObywatel app. This page\n  continues on its own once you're logged in.": 'Powinno otworzyć się okno Chrome — zeskanuj kod QR w aplikacji mObywatel. Po zalogowaniu strona przejdzie dalej automatycznie.',
    'This tool is for improving an existing booked exam. You need a current booking and its date; it alerts you only when it finds an earlier slot.': 'To narzędzie służy do przyspieszania już zarezerwowanego egzaminu. Potrzebujesz aktualnej rezerwacji i jej daty; powiadamia tylko o wcześniejszych terminach.',
    'Required: date of your existing booked slot': 'Wymagane: data obecnie zarezerwowanego terminu',
    'This required date is the baseline for alerts: the app notifies you only about earlier slots.': 'Ta wymagana data jest punktem odniesienia dla powiadomień: aplikacja informuje tylko o wcześniejszych terminach.',
    'Open browser': 'Otwórz przeglądarkę', 'Quit': 'Zakończ', 'Get new session': 'Pobierz nową sesję',
    'Paused': 'Wstrzymano', 'Click to resume': 'Kliknij, aby wznowić', 'Click to pause': 'Kliknij, aby wstrzymać',
    'Dashboard lost contact with the notifier': 'Panel utracił połączenie z programem', 'spots': 'miejsc',
    'Log back in via browser and update session.json': 'Zaloguj się ponownie w przeglądarce i zaktualizuj session.json',
    "Can't reach info-kierowca.pl — will retry": 'Nie można połączyć się z info-kierowca.pl — ponowna próba nastąpi automatycznie',
    'Unexpected response — check manually': 'Nieoczekiwana odpowiedź — sprawdź ręcznie', 'Last checked': 'Ostatnie sprawdzenie',
    'No checks yet': 'Brak sprawdzeń', 'Session expires in': 'Sesja wygaśnie za', 'min': 'min',
    'no slots in the next 31 days': 'brak terminów w ciągu najbliższych 31 dni', 'Next check in': 'Następne sprawdzenie za',
    'Sending...': 'Wysyłanie...', 'Sent — check your phone.': 'Wysłano — sprawdź telefon.', 'Failed to send.': 'Nie udało się wysłać.',
    "This logs you out and clears your saved settings. You'll need to scan the QR code again. Continue?": 'Spowoduje to wylogowanie i usunięcie zapisanych ustawień. Trzeba będzie ponownie zeskanować kod QR. Kontynuować?',
    'Reset failed — check the log.': 'Resetowanie nie powiodło się — sprawdź dziennik.', 'Pick at least one exam type.': 'Wybierz co najmniej jeden rodzaj egzaminu.',
    'Pick at least one WORD center.': 'Wybierz co najmniej jeden ośrodek WORD.', 'PKK number is required.': 'Numer PKK jest wymagany.',
    'Pick a license category.': 'Wybierz kategorię prawa jazdy.', 'Pick the date of your current booked slot.': 'Wybierz datę obecnie zarezerwowanego terminu.', 'Save failed.': 'Nie udało się zapisać.',
    'Settings saved.': 'Ustawienia zapisane.', 'Checking…': 'Sprawdzanie…',
    'Quit info-kierowca-notifier? You will stop getting checked/notified until you start it again.': 'Zakończyć info-kierowca-notifier? Sprawdzanie i powiadomienia zostaną wstrzymane do następnego uruchomienia.',
    'Stopped. You can close this tab.': 'Zatrzymano. Możesz zamknąć tę kartę.',
    'Open Chrome for a fresh QR login now? This replaces your current session.': 'Otworzyć Chrome, aby ponownie zalogować się kodem QR? Zastąpi to obecną sesję.',
    'Could not open Chrome — try the manual option below.': 'Nie udało się otworzyć Chrome — spróbuj opcji ręcznej poniżej.',
    "Login didn't complete — the Chrome window may have been closed. Try again.": 'Logowanie nie zostało ukończone — okno Chrome mogło zostać zamknięte. Spróbuj ponownie.',
    "This lets the app automatically click through and submit a real reservation date change the moment it finds a matching slot — no review step, and it can't be undone by closing the browser. Are you sure?": 'To pozwala programowi automatycznie przejść dalej i wysłać rzeczywistą zmianę daty rezerwacji po znalezieniu pasującego terminu — bez etapu sprawdzania i bez możliwości cofnięcia przez zamknięcie przeglądarki. Czy na pewno chcesz kontynuować?',
    'Invalid request.': 'Nieprawidłowe żądanie.', 'Notification topic is required': 'Temat powiadomień jest wymagany',
    'PKK number is required': 'Numer PKK jest wymagany', 'Pick at least one WORD center': 'Wybierz co najmniej jeden ośrodek WORD',
    'WORD center IDs must be numeric IDs': 'Identyfikatory ośrodków WORD muszą być liczbami', 'Pick at least one exam type': 'Wybierz co najmniej jeden rodzaj egzaminu',
    'Category must be a number': 'Kategoria musi być liczbą', 'Check frequency must be a number': 'Częstotliwość sprawdzania musi być liczbą',
    'Preferred time window must be numbers': 'Preferowany przedział godzin musi zawierać liczby', 'Preferred time window must be a valid range between 00:00 and 24:00': 'Preferowany przedział godzin musi być poprawnym zakresem od 00:00 do 24:00',
    'Current slot date is required': 'Data obecnego terminu jest wymagana', 'Current slot date must be a date like 2026-09-14': 'Data obecnego terminu musi mieć format 2026-09-14',
    'Something went wrong.': 'Coś poszło nie tak.', 'Could not reach the app.': 'Nie można połączyć się z programem.',
    'Paused — checking will stop until you resume.': 'Wstrzymano — sprawdzanie nie będzie działać do momentu wznowienia.', 'Resumed checking.': 'Wznowiono sprawdzanie.',
    'Toggle pause': 'Przełącz wstrzymanie', 'No notification topic set yet.': 'Nie ustawiono jeszcze tematu powiadomień.',
    'Session looks valid — opening a logged-in browser tab.': 'Sesja wygląda na ważną — otwieranie zalogowanej karty przeglądarki.',
    'A logged-in browser tab is already open.': 'Zalogowana karta przeglądarki jest już otwarta.',
    'Session looks valid, but auto_open_browser is turned off in Settings.': 'Sesja wygląda na ważną, ale auto_open_browser jest wyłączone w Ustawieniach.',
    'Session looks valid, but the browser failed to launch — check the log.': 'Sesja wygląda na ważną, ale przeglądarka nie została uruchomiona — sprawdź dziennik.',
    'Session looks valid, but no Chrome, Edge, or other Chromium-based browser was found on this machine — install one to continue.': 'Sesja wygląda na ważną, ale na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium — zainstaluj ją, aby kontynuować.',
    'Session looks expired — opening Chrome for a fresh QR login.': 'Sesja wygląda na wygasłą — otwieranie Chrome do ponownego logowania kodem QR.',
    'Session looks expired, but auto_refresh_chrome is turned off in Settings.': 'Sesja wygląda na wygasłą, ale auto_refresh_chrome jest wyłączone w Ustawieniach.',
    'Session looks expired, but Chrome failed to launch — check the log.': 'Sesja wygląda na wygasłą, ale Chrome nie zostało uruchomione — sprawdź dziennik.',
    'Session looks expired, but no Chrome, Edge, or other Chromium-based browser was found on this machine — install one to continue.': 'Sesja wygląda na wygasłą, ale na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium — zainstaluj ją, aby kontynuować.',
    'Opening Chrome for a fresh QR login.': 'Otwieranie Chrome do ponownego logowania kodem QR.',
    'auto_refresh_chrome is turned off in Settings.': 'auto_refresh_chrome jest wyłączone w Ustawieniach.',
    'Chrome failed to launch — check the log.': 'Chrome nie zostało uruchomione — sprawdź dziennik.',
    'No Chrome, Edge, or other Chromium-based browser was found on this machine — install one to continue.': 'Na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium — zainstaluj ją, aby kontynuować.',
    'No Chrome, Edge, or other Chromium-based browser was found on this machine. Install one and try again.': 'Na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium. Zainstaluj ją i spróbuj ponownie.',
    'Session expired': 'Sesja wygasła', 'Offline': 'Offline', "Something's wrong": 'Coś poszło nie tak', 'No slots in the next 31 days': 'Brak terminów w ciągu najbliższych 31 dni', 'Waiting for first check…': 'Oczekiwanie na pierwsze sprawdzenie…', 'Checking any moment now…': 'Sprawdzenie nastąpi za chwilę…'
  };
  const originals = new WeakMap();
  function lang() { return localStorage.getItem(KEY) === 'pl' ? 'pl' : 'en'; }
  function t(text) {
    if (lang() !== 'pl') return text;
    if (PL[text]) return PL[text];
    let match = text.match(/^Pick at most (\d+) WORD centers — the site's search only accepts \1 at a time\.?$/);
    if (match) return `Wybierz najwyżej ${match[1]} ośrodków WORD — wyszukiwarka strony przyjmuje jednocześnie tylko ${match[1]}.`;
    match = text.match(/^Pick at most (\d+) WORD centers — the site's search only accepts that many at a time$/);
    if (match) return `Wybierz najwyżej ${match[1]} ośrodków WORD — wyszukiwarka strony przyjmuje jednocześnie tylko tyle.`;
    match = text.match(/^Check frequency must be between (\d+) and (\d+) seconds$/);
    if (match) return `Częstotliwość sprawdzania musi mieścić się między ${match[1]} a ${match[2]} sekundami.`;
    return text;
  }
  function translateNode(node) {
    if (!originals.has(node)) originals.set(node, node.nodeValue);
    const source = originals.get(node); const trimmed = source.trim(); const translated = t(trimmed);
    node.nodeValue = translated === trimmed ? source : source.replace(trimmed, translated);
  }
  function apply() {
    document.documentElement.lang = lang();
    document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {acceptNode(n) {
      return n.parentElement && !['SCRIPT', 'STYLE'].includes(n.parentElement.tagName) && n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }});
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode); nodes.forEach(translateNode);
    document.querySelectorAll('[title],[aria-label],[placeholder]').forEach(el => ['title','aria-label','placeholder'].forEach(a => {
      if (!el.hasAttribute(a)) return;
      const original = `data-ikw-original-${a}`;
      if (!el.hasAttribute(original)) el.setAttribute(original, el.getAttribute(a));
      el.setAttribute(a, t(el.getAttribute(original)));
    }));
    document.querySelectorAll('.ikw-language-switch').forEach(s => s.querySelectorAll('button').forEach(b => {
      const active = b.dataset.lang === lang(); b.classList.toggle('active', active); b.setAttribute('aria-pressed', String(active));
    }));
    document.title = t(document.title);
  }
  function installSwitcher(target) {
    if (!target || target.querySelector('.ikw-language-switch')) return;
    const box = document.createElement('div'); box.className = 'ikw-language-switch'; box.setAttribute('role', 'group'); box.setAttribute('aria-label', 'Language / Język');
    box.innerHTML = '<button type="button" data-lang="en" aria-label="English">EN</button><span aria-hidden="true">·</span><button type="button" data-lang="pl" aria-label="Polski">PL</button>';
    box.querySelectorAll('button').forEach(b => b.addEventListener('click', () => { localStorage.setItem(KEY, b.dataset.lang); apply(); window.dispatchEvent(new Event('ikw-language-changed')); if (window.parent !== window) window.parent.postMessage({type: 'ikw-language-changed'}, window.location.origin); }));
    target.appendChild(box); apply();
  }
  window.ikwI18n = { apply, installSwitcher, t, lang };
  window.addEventListener('ikw-language-changed', apply);
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
      if (node.nodeType === Node.TEXT_NODE) translateNode(node);
      else if (node.nodeType === Node.ELEMENT_NODE) {
        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) if (walker.currentNode.parentElement && !['SCRIPT', 'STYLE'].includes(walker.currentNode.parentElement.tagName)) translateNode(walker.currentNode);
      }
    }))).observe(document.body, {childList: true, subtree: true});
  });
})();
</script>
"""
