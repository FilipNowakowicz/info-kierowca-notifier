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
    'Search mode': 'Tryb wyszukiwania', 'Single center': 'Jeden ośrodek', 'Multiple centers': 'Wiele ośrodków',
    'All dates made available by the selected WORD': 'Wszystkie terminy udostępnione przez wybrany WORD', 'Earliest slots made available by the selected WORD centers (up to 5 centers)': 'Najbliższe terminy udostępnione przez wybrane WORD (do 5 ośrodków)',
    'Single-center mode — searching all available dates.': 'Tryb pojedynczego ośrodka — wyszukiwanie wszystkich dostępnych terminów.',
    'Alerts': 'Powiadomienia', 'Automation': 'Automatyzacja', 'WORD centers to watch (__CENTER_COUNT__ nationwide)': 'Ośrodki WORD do obserwowania (__CENTER_COUNT__ w kraju)',
    'Click to browse all centers, or type to filter...': 'Kliknij, aby przeglądać ośrodki, lub wpisz tekst, aby filtrować...',
    'Date of your current booked slot — a found slot on an earlier date beats this and triggers the alerts below': 'Data obecnie zarezerwowanego terminu — wolny termin wcześniejszy niż ten uruchomi poniższe powiadomienia',
    'Select a date': 'Wybierz datę', 'Preferred time of day': 'Preferowana pora dnia', 'All day': 'Cały dzień',
    "A slot outside this window won't trigger an alert or open the reservation browser. Checking and the dashboard still show everything found.": 'Termin poza tym przedziałem nie uruchomi powiadomienia ani przeglądarki rezerwacji. Sprawdzanie i panel nadal pokażą wszystkie znalezione terminy.',
    'Send a phone alert when a slot beats your booked date': 'Wyślij powiadomienie na telefon, gdy termin jest wcześniejszy',
    'Buzzes your phone when a watched center opens a slot on or before your booked date. Turn off to just watch the dashboard.': 'Powiadamia telefon, gdy obserwowany ośrodek udostępni termin w dniu obecnej rezerwacji lub wcześniej. Wyłącz, aby tylko obserwować panel.',
    'Send a phone alert when your session expires': 'Wyślij powiadomienie na telefon po wygaśnięciu sesji',
    'Buzzes your phone when your login expires and Chrome reopens for you to scan the QR again. Turn off to only get the desktop popup.': 'Powiadamia telefon, gdy logowanie wygaśnie i Chrome otworzy się ponownie, aby zeskanować kod QR. Wyłącz, aby otrzymywać tylko komunikat na komputerze.',
    'Buzzes your phone when your login expires and needs a fresh Profil Zaufany login. Turn off to only get the desktop popup.': 'Powiadamia telefon, gdy logowanie wygaśnie i będzie potrzebne ponowne logowanie przez Profil Zaufany. Wyłącz, aby otrzymywać tylko komunikat na komputerze.',
    'Your private notification link — install the': 'Twój prywatny link powiadomień — zainstaluj aplikację', 'and subscribe to it exactly:': 'i zasubskrybuj dokładnie ten link:',
    'Copy link': 'Kopiuj link', "Anyone who knows this link can read your notifications — don't share it.": 'Każdy, kto zna ten link, może odczytać powiadomienia — nie udostępniaj go.',
    'Send test push': 'Wyślij testowe powiadomienie', 'Check frequency': 'Częstotliwość sprawdzania',
    'Run automatic Profil Zaufany login in the background': 'Uruchamiaj automatyczne logowanie przez Profil Zaufany w tle',
    'Starts Chrome headlessly so no window pops up. Google Messages must already be paired.': 'Uruchamia Chrome bez interfejsu, więc nie pojawia się żadne okno. Wiadomości Google muszą być wcześniej sparowane.',
    'info-kierowca.pl allows about 10 searches per hour. Faster than every 6 minutes uses them up early in the hour; the app then waits for the limit to reset instead of going over it.': 'info-kierowca.pl pozwala na około 10 wyszukiwań na godzinę. Sprawdzanie częściej niż co 6 minut wyczerpuje je na początku godziny; program czeka wtedy na odnowienie limitu, zamiast go przekraczać.',
    'Open the reservation page when an earlier slot is found': 'Otwórz rezerwację, gdy znajdzie się wcześniejszy termin', 'Opens a logged-in browser on the available-exams screen for the WORD where the better slot was found. You still choose the slot yourself.': 'Otwiera zalogowaną przeglądarkę na ekranie dostępnych egzaminów dla WORD-u, w którym znaleziono wcześniejszy termin. Termin nadal wybierasz samodzielnie.',
    'Auto-select the matching slot': 'Automatycznie wybierz pasujący termin', 'Also selects the exact matching date/time and advances through the reservation wizard to the preliminary reservation summary. Stops there — nothing is submitted.': 'Dodatkowo wybiera dokładnie pasującą datę i godzinę oraz przechodzi przez kolejne kroki rezerwacji do ekranu podsumowania wstępnej rezerwacji. Na tym się zatrzymuje — nic nie jest wysyłane.',
    'Auto-confirm the reservation': 'Automatycznie utwórz rezerwację', 'Requires the toggle above. After verifying the preliminary summary, creates the reservation and leaves the browser open for any required confirmation/payment. This action cannot be undone by simply closing the browser. Your existing booking is not cancelled — you may need to cancel it yourself on info-kierowca.pl.': 'Wymaga powyższego przełącznika. Po sprawdzeniu podsumowania wstępnej rezerwacji tworzy rezerwację i pozostawia przeglądarkę otwartą na potrzeby ewentualnego potwierdzenia lub płatności. Tej czynności nie można cofnąć przez samo zamknięcie przeglądarki. Twoja obecna rezerwacja nie zostanie anulowana — może być konieczne samodzielne anulowanie jej w info-kierowca.pl.',
    'Get new session now': 'Pobierz nową sesję teraz',
    'Save and log in': 'Zapisz i zaloguj', 'Save changes': 'Zapisz zmiany', 'Reset account': 'Zresetuj konto',
    "Logs you out and clears your saved settings — you'll land back on the login screen to reconnect your account.": 'Wylogowuje i usuwa zapisane ustawienia — wrócisz do ekranu logowania, aby ponownie połączyć konto.',
    'The new Profil Zaufany account was saved, but the previous credential could not be removed from the operating-system credential store.': 'Nowe konto Profilu Zaufanego zostało zapisane, ale nie udało się usunąć poprzednich danych logowania z magazynu poświadczeń systemu operacyjnego.',
    'Local account data was reset, but the saved Profil Zaufany credential could not be removed from the operating-system credential store.': 'Lokalne dane konta zostały zresetowane, ale nie udało się usunąć zapisanych danych logowania Profilu Zaufanego z magazynu poświadczeń systemu operacyjnego.',
    'Back to dashboard': 'Wróć do panelu', 'Show or hide PKK number': 'Pokaż lub ukryj numer PKK', 'Show or hide Profil Zaufany email address': 'Pokaż lub ukryj adres e-mail użytkownika Profilu Zaufanego', 'Show or hide notification link': 'Pokaż lub ukryj link powiadomień',
    'No centers yet — search above to add one.': 'Nie wybrano jeszcze ośrodków — wyszukaj powyżej, aby dodać.', 'Remove': 'Usuń', 'No matching centers.': 'Brak pasujących ośrodków.', 'All centers added.': 'Dodano wszystkie ośrodki.',
    'Connect your account': 'Połącz konto', 'Log in with mObywatel': 'Zaloguj się przez mObywatel', 'Waiting for QR scan...': 'Oczekiwanie na skan kodu QR...', 'Skip and enter my PKK number manually': 'Pomiń i wpisz numer PKK ręcznie',
    'Choose how the notifier should authenticate. Profil Zaufany can recover expired sessions automatically after setup.': 'Wybierz sposób uwierzytelniania. Po konfiguracji Profil Zaufany może automatycznie odnawiać wygasłe sesje.',
    'Profil Zaufany email address': 'Adres e-mail użytkownika Profilu Zaufanego', 'Profil Zaufany password': 'Hasło Profilu Zaufanego',
    'Pair Google Messages Web': 'Sparuj Google Messages Web',
    'Required for automatic Profil Zaufany login: pairing lets the app read the one-time SMS code from Google Messages.': 'Wymagane do automatycznego logowania przez Profil Zaufany: sparowanie pozwala aplikacji odczytać jednorazowy kod SMS z Wiadomości Google.',
    'Log in with Profil Zaufany': 'Zaloguj się przez Profil Zaufany', 'Authenticating automatically...': 'Automatyczne uwierzytelnianie...',
    'Authentication': 'Uwierzytelnianie', 'Authentication method': 'Metoda uwierzytelniania',
    'Leave blank to keep the saved password': 'Pozostaw puste, aby zachować zapisane hasło',
    'Password saved securely by your operating system; its value is never shown here.': 'Hasło jest bezpiecznie zapisane przez system operacyjny; jego wartość nigdy nie jest tutaj wyświetlana.',
    'No Profil Zaufany password is saved. Enter it to enable automatic login.': 'Hasło Profilu Zaufanego nie jest zapisane. Wpisz je, aby włączyć automatyczne logowanie.',
    'Google Messages Web opened for pairing.': 'Otwarto Google Messages Web do parowania.', 'Could not open Google Messages Web.': 'Nie udało się otworzyć Google Messages Web.',
    'SMS extraction is working — a PZePUAP verification message was detected.': 'Odczyt SMS działa — wykryto wiadomość weryfikacyjną PZePUAP.',
    'PZePUAP conversation found, but no current OTP was detected.': 'Znaleziono rozmowę PZePUAP, ale nie wykryto aktualnego kodu.',
    'Only a stale PZePUAP OTP was found.': 'Znaleziono tylko nieaktualny kod PZePUAP.', 'PZePUAP conversation was not found.': 'Nie znaleziono rozmowy PZePUAP.',
    'Google Messages is not paired.': 'Google Messages nie jest sparowane.', 'Google Messages page structure is unsupported.': 'Struktura strony Google Messages nie jest obsługiwana.',
    'Google Messages target is unavailable.': 'Karta Google Messages jest niedostępna.', 'SMS extraction test failed.': 'Test odczytu SMS nie powiódł się.',
    "Log in once via the mObywatel QR code — this is what lets the notifier check for\n  slots on your behalf. While we're at it, we'll also find your PKK number and license category\n  automatically, so you don't have to type them in.": 'Zaloguj się raz kodem QR w aplikacji mObywatel — dzięki temu program może sprawdzać terminy w Twoim imieniu. Przy okazji automatycznie znajdzie numer PKK i kategorię prawa jazdy, więc nie trzeba ich wpisywać ręcznie.',
    "A Chrome window should open — scan the QR code in the mObywatel app. This page continues on its own once you're logged in.": 'Powinno otworzyć się okno Chrome — zeskanuj kod QR w aplikacji mObywatel. Po zalogowaniu strona przejdzie dalej automatycznie.',
    "Logging in automatically with your saved Profil Zaufany credentials and the SMS code from Google Messages. This page continues on its own once you're logged in.": 'Trwa automatyczne logowanie przy użyciu zapisanych danych Profilu Zaufanego i kodu SMS z Wiadomości Google. Po zalogowaniu strona przejdzie dalej automatycznie.',
    'Enter your currently booked exam date. The app uses it only as a comparison point for finding earlier slots — it does not change or cancel your existing reservation.': 'Wpisz datę obecnie zarezerwowanego egzaminu. Aplikacja używa jej wyłącznie jako punktu odniesienia do wyszukiwania wcześniejszych terminów — nie zmienia ani nie anuluje istniejącej rezerwacji.',
    'Got it': 'Rozumiem', 'Dismiss booking prerequisite message': 'Ukryj informację o wymaganej istniejącej rezerwacji',
    'Required: current booked exam date': 'Wymagane: data obecnie zarezerwowanego egzaminu',
    'This date is only a comparison point for alerts and reservation automation. If you have an active reservation, the portal may require you to cancel it and wait for cancellation confirmation before creating a new one.': 'Ta data służy wyłącznie jako punkt odniesienia dla powiadomień i automatyzacji rezerwacji. Jeśli masz aktywną rezerwację, portal może wymagać jej wcześniejszego anulowania i oczekiwania na potwierdzenie anulowania przed utworzeniem nowej.',
    'Earliest acceptable exam date (optional)': 'Najwcześniejsza akceptowalna data egzaminu (opcjonalnie)',
    'Earliest search date: from 2 days from today up to 6 months ahead.': 'Najwcześniejsza data wyszukiwania: od 2 dni od dzisiaj do 6 miesięcy naprzód.',
    'Clear earliest acceptable date': 'Wyczyść najwcześniejszą akceptowalną datę',
    'Open browser': 'Otwórz przeglądarkę', 'Quit': 'Zakończ', 'Get new session': 'Pobierz nową sesję',
    'Paused': 'Wstrzymano', 'Click to resume': 'Kliknij, aby wznowić', 'Click to pause': 'Kliknij, aby wstrzymać',
    'Dashboard lost contact with the notifier': 'Panel utracił połączenie z programem', 'spots': 'miejsc', 'Available now': 'Dostępne teraz', 'Last confirmed available slots': 'Ostatnio potwierdzone dostępne terminy',
    'Rate limit reached': 'Osiągnięto limit wyszukiwań', 'Last successful search': 'Ostatnie prawidłowe wyszukiwanie',
    'The slots below are from the last successful search and may no longer be current.': 'Poniższe terminy pochodzą z ostatniego prawidłowego wyszukiwania i mogą być już nieaktualne.',
    'Log back in via browser and update session.json': 'Zaloguj się ponownie w przeglądarce i zaktualizuj session.json',
    "Can't reach info-kierowca.pl — will retry": 'Nie można połączyć się z info-kierowca.pl — ponowna próba nastąpi automatycznie',
    'Unexpected response — check manually': 'Nieoczekiwana odpowiedź — sprawdź ręcznie', 'Last checked': 'Ostatnie sprawdzenie',
    'No checks yet': 'Brak sprawdzeń', 'Session expires in': 'Sesja wygaśnie za', 'min': 'min',
    'No available slots found': 'Brak dostępnych terminów', 'Next check in': 'Następne sprawdzenie za',
    'Sending...': 'Wysyłanie...', 'Sent — check your phone.': 'Wysłano — sprawdź telefon.', 'Failed to send.': 'Nie udało się wysłać.',
    'Could not reach ntfy. Check your network connection.': 'Nie można połączyć się z ntfy. Sprawdź połączenie z internetem.',
    'TLS certificate verification failed. Check your system trust settings.': 'Weryfikacja certyfikatu TLS nie powiodła się. Sprawdź ustawienia zaufanych certyfikatów systemu.',
    'TLS trust configuration is invalid. Check the configured CA bundle.': 'Konfiguracja zaufanych certyfikatów TLS jest nieprawidłowa. Sprawdź skonfigurowany pakiet CA.',
    "This logs you out and clears your saved settings. You'll need to log in again. Continue?": 'Spowoduje to wylogowanie i usunięcie zapisanych ustawień. Trzeba będzie zalogować się ponownie. Kontynuować?',
    'Reset failed — check the log.': 'Resetowanie nie powiodło się — sprawdź dziennik.', 'Pick at least one exam type.': 'Wybierz co najmniej jeden rodzaj egzaminu.',
    'Pick at least one WORD center.': 'Wybierz co najmniej jeden ośrodek WORD.', 'PKK number is required.': 'Numer PKK jest wymagany.',
    'Pick a license category.': 'Wybierz kategorię prawa jazdy.', 'Pick the date of your current booked slot.': 'Wybierz datę obecnie zarezerwowanego terminu.', 'Save failed.': 'Nie udało się zapisać.',
    'Settings saved.': 'Ustawienia zapisane.', 'Checking…': 'Sprawdzanie…',
    'Quit info-kierowca-notifier? You will stop getting checked/notified until you start it again.': 'Zakończyć info-kierowca-notifier? Sprawdzanie i powiadomienia zostaną wstrzymane do następnego uruchomienia.',
    'Stopped. You can close this tab.': 'Zatrzymano. Możesz zamknąć tę kartę.',
    'Open Chrome for a fresh QR login now? This replaces your current session.': 'Otworzyć Chrome, aby ponownie zalogować się kodem QR? Zastąpi to obecną sesję.',
    'Log back in with Profil Zaufany now? This replaces your current session.': 'Zalogować się ponownie przez Profil Zaufany? Zastąpi to obecną sesję.',
    'A QR login is already open. Close it and restart login?': 'Logowanie kodem QR jest już otwarte. Zamknąć je i rozpocząć ponownie?',
    'A Profil Zaufany login is already running. Close it and restart login?': 'Logowanie przez Profil Zaufany już trwa. Zamknąć je i rozpocząć ponownie?',
    'A Profil Zaufany login is already running — give it a moment to finish.': 'Logowanie przez Profil Zaufany już trwa — poczekaj chwilę, aż się zakończy.',
    'Could not restart the QR login.': 'Nie udało się ponownie uruchomić logowania kodem QR.',
    'Could not restart the Profil Zaufany login.': 'Nie udało się ponownie uruchomić logowania przez Profil Zaufany.',
    'The previous QR login closed — opening a fresh one.': 'Poprzednie logowanie kodem QR zostało zamknięte — otwieranie nowego.',
    'The previous Profil Zaufany login closed — opening a fresh one.': 'Poprzednie logowanie przez Profil Zaufany zostało zamknięte — otwieranie nowego.',
    'That older QR login cannot be restarted safely. Close its Chrome window, then try again.': 'Tego starszego logowania kodem QR nie można bezpiecznie uruchomić ponownie. Zamknij jego okno Chrome i spróbuj ponownie.',
    'That older Profil Zaufany login cannot be restarted safely. Close its Chrome window, then try again.': 'Tego starszego logowania przez Profil Zaufany nie można bezpiecznie uruchomić ponownie. Zamknij jego okno Chrome i spróbuj ponownie.',
    'The existing QR login did not close. No second browser was opened.': 'Istniejące logowanie kodem QR nie zostało zamknięte. Nie otwarto drugiej przeglądarki.',
    'The existing Profil Zaufany login did not close. No second browser was opened.': 'Istniejące logowanie przez Profil Zaufany nie zostało zamknięte. Nie otwarto drugiej przeglądarki.',
    'A QR login is still running. No second browser was opened.': 'Logowanie kodem QR nadal działa. Nie otwarto drugiej przeglądarki.',
    'A Profil Zaufany login is still running. No second browser was opened.': 'Logowanie przez Profil Zaufany nadal trwa. Nie otwarto drugiej przeglądarki.',
    'The old QR login closed, but Chrome failed to relaunch — check the log.': 'Poprzednie logowanie kodem QR zostało zamknięte, ale Chrome nie uruchomił się ponownie — sprawdź dziennik.',
    'The old Profil Zaufany login closed, but Chrome failed to relaunch — check the log.': 'Poprzednie logowanie przez Profil Zaufany zostało zamknięte, ale Chrome nie uruchomił się ponownie — sprawdź dziennik.',
    'Could not open Chrome — try the manual option below.': 'Nie udało się otworzyć Chrome — spróbuj opcji ręcznej poniżej.',
    "Login didn't complete — the Chrome window may have been closed. Try again.": 'Logowanie nie zostało ukończone — okno Chrome mogło zostać zamknięte. Spróbuj ponownie.',
    "After the matching slot is selected and the preliminary summary is verified, the app will submit a real reservation through the portal. The portal may then require confirmation or payment. Closing the browser does not undo a submitted reservation. Your existing booking is not cancelled — you may need to cancel it yourself on info-kierowca.pl. Are you sure?": 'Po wybraniu pasującego terminu i sprawdzeniu podsumowania wstępnej rezerwacji program wyśle rzeczywistą rezerwację przez portal. Portal może następnie wymagać potwierdzenia lub płatności. Zamknięcie przeglądarki nie cofa wysłanej rezerwacji. Twoja obecna rezerwacja nie zostanie anulowana — może być konieczne samodzielne anulowanie jej w info-kierowca.pl. Czy na pewno chcesz kontynuować?',
    'Invalid request.': 'Nieprawidłowe żądanie.', 'Notification topic is required': 'Temat powiadomień jest wymagany',
    'PKK number is required': 'Numer PKK jest wymagany', 'Pick at least one WORD center': 'Wybierz co najmniej jeden ośrodek WORD',
    'Choose a supported search mode': 'Wybierz obsługiwany tryb wyszukiwania', 'Single-center mode requires exactly one WORD center': 'Tryb jednego ośrodka wymaga wybrania dokładnie jednego ośrodka WORD',
    'WORD center IDs must be numeric IDs': 'Identyfikatory ośrodków WORD muszą być liczbami', 'Pick at least one exam type': 'Wybierz co najmniej jeden rodzaj egzaminu',
    'Category must be a number': 'Kategoria musi być liczbą', 'Check frequency must be a number': 'Częstotliwość sprawdzania musi być liczbą',
    'Preferred time window must be numbers': 'Preferowany przedział godzin musi zawierać liczby', 'Preferred time window must be a valid range between 00:00 and 24:00': 'Preferowany przedział godzin musi być poprawnym zakresem od 00:00 do 24:00',
    'Current slot date is required': 'Data obecnego terminu jest wymagana', 'Current slot date must be a date like 2026-09-14': 'Data obecnego terminu musi mieć format 2026-09-14',
    'Earliest acceptable exam date must be a date like 2026-09-14': 'Najwcześniejsza akceptowalna data egzaminu musi mieć format 2026-09-14',
    'Earliest acceptable exam date must be at least 2 days from today': 'Najwcześniejsza akceptowalna data egzaminu musi przypadać co najmniej 2 dni od dzisiaj', 'Earliest acceptable exam date must be within the next 6 months': 'Najwcześniejsza akceptowalna data egzaminu musi przypadać w ciągu najbliższych 6 miesięcy',
    'Earliest acceptable exam date must be before the current booking date': 'Najwcześniejsza akceptowalna data egzaminu musi przypadać przed datą obecnej rezerwacji',
    'Something went wrong.': 'Coś poszło nie tak.', 'Could not reach the app.': 'Nie można połączyć się z programem.',
    'Paused — checking will stop until you resume.': 'Wstrzymano — sprawdzanie nie będzie działać do momentu wznowienia.', 'Resumed checking.': 'Wznowiono sprawdzanie.',
    'The search limit has expired. Check for available slots now? Choose Cancel to start the normal countdown instead.': 'Limit wyszukiwań już wygasł. Sprawdzić dostępne terminy teraz? Wybierz Anuluj, aby zamiast tego rozpocząć standardowe odliczanie.',
    'Toggle pause': 'Przełącz wstrzymanie', 'No notification topic set yet.': 'Nie ustawiono jeszcze tematu powiadomień.',
    'Session looks valid — opening a logged-in browser tab.': 'Sesja wygląda na ważną — otwieranie zalogowanej karty przeglądarki.',
    'A logged-in browser tab is already open.': 'Zalogowana karta przeglądarki jest już otwarta.',
    'Session looks valid, but auto_open_browser is turned off in Settings.': 'Sesja wygląda na ważną, ale auto_open_browser jest wyłączone w Ustawieniach.',
    'Session looks valid, but the browser failed to launch — check the log.': 'Sesja wygląda na ważną, ale przeglądarka nie została uruchomiona — sprawdź dziennik.',
    'Session looks valid, but no Chrome, Edge, or other Chromium-based browser was found on this machine — install one to continue.': 'Sesja wygląda na ważną, ale na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium — zainstaluj ją, aby kontynuować.',
    'Session looks expired — opening Chrome for a fresh QR login.': 'Sesja wygląda na wygasłą — otwieranie Chrome do ponownego logowania kodem QR.',
    'Session looks expired — opening Chrome for a fresh Profil Zaufany login.': 'Sesja wygląda na wygasłą — otwieranie Chrome do ponownego logowania przez Profil Zaufany.',
    'A QR login is already open — finish scanning there.': 'Logowanie kodem QR jest już otwarte — dokończ skanowanie tam.',
    'Session looks expired, but auto_refresh_chrome is turned off in Settings.': 'Sesja wygląda na wygasłą, ale auto_refresh_chrome jest wyłączone w Ustawieniach.',
    'Session looks expired, but Chrome failed to launch — check the log.': 'Sesja wygląda na wygasłą, ale Chrome nie zostało uruchomione — sprawdź dziennik.',
    'Session looks expired, but no Chrome, Edge, or other Chromium-based browser was found on this machine — install one to continue.': 'Sesja wygląda na wygasłą, ale na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium — zainstaluj ją, aby kontynuować.',
    'Opening Chrome for a fresh QR login.': 'Otwieranie Chrome do ponownego logowania kodem QR.',
    'Opening Chrome for a fresh Profil Zaufany login.': 'Otwieranie Chrome do ponownego logowania przez Profil Zaufany.',
    'auto_refresh_chrome is turned off in Settings.': 'auto_refresh_chrome jest wyłączone w Ustawieniach.',
    'Chrome failed to launch — check the log.': 'Chrome nie zostało uruchomione — sprawdź dziennik.',
    'No Chrome, Edge, or other Chromium-based browser was found on this machine — install one to continue.': 'Na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium — zainstaluj ją, aby kontynuować.',
    'No Chrome, Edge, or other Chromium-based browser was found on this machine. Install one and try again.': 'Na tym komputerze nie znaleziono Chrome, Edge ani innej przeglądarki opartej na Chromium. Zainstaluj ją i spróbuj ponownie.',
    'Session expired': 'Sesja wygasła', 'Offline': 'Offline', "Something's wrong": 'Coś poszło nie tak', 'Waiting for first check…': 'Oczekiwanie na pierwsze sprawdzenie…', 'Waiting for next check…': 'Oczekiwanie na kolejne sprawdzenie…', 'Checking any moment now…': 'Sprawdzenie nastąpi za chwilę…',
    'Session expired — manual retry required': 'Sesja wygasła — wymagane ręczne ponowienie',
    'Automatic login is paused after repeated failures — open Settings and click "Get new session now."': 'Automatyczne logowanie jest wstrzymane po kilku nieudanych próbach — otwórz Ustawienia i kliknij „Pobierz nową sesję teraz”.',
  };
  const originals = new WeakMap();
  function lang() { return localStorage.getItem(KEY) === 'pl' ? 'pl' : 'en'; }
  function places(count) {
    const n = Number(count);
    if (lang() !== 'pl') return `${n} ${n === 1 ? 'spot' : 'spots'}`;
    const abs = Math.abs(n); const mod10 = abs % 10; const mod100 = abs % 100;
    const word = n === 1 ? 'miejsce' : (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14) ? 'miejsca' : 'miejsc');
    return `${n} ${word}`;
  }
  function t(text) {
    if (lang() !== 'pl') return text;
    if (PL[text]) return PL[text];
    let match = text.match(/^Pick at most (\d+) WORD centers — the site's search only accepts \1 at a time\.?$/);
    if (match) return `Wybierz najwyżej ${match[1]} ośrodków WORD — wyszukiwarka strony przyjmuje jednocześnie tylko ${match[1]}.`;
    match = text.match(/^Pick at most (\d+) WORD centers — the site's search only accepts that many at a time$/);
    if (match) return `Wybierz najwyżej ${match[1]} ośrodków WORD — wyszukiwarka strony przyjmuje jednocześnie tylko tyle.`;
    match = text.match(/^Check frequency must be between (\d+) and (\d+) seconds$/);
    if (match) return `Częstotliwość sprawdzania musi mieścić się między ${match[1]} a ${match[2]} sekundami.`;
    match = text.match(/^WORD centers to watch \((\d+) nationwide\)$/);
    if (match) return `Ośrodki WORD do obserwowania (${match[1]} w kraju)`;
    match = text.match(/^Watching (\d+) of (\d+) centers for open slots\.$/);
    if (match) return `Obserwujesz ${match[1]} z ${match[2]} ośrodków pod kątem wolnych terminów.`;
    match = text.match(/^Every (.+)$/);
    if (match) return `Co ${match[1]}`;
    match = text.match(/^The search limit expired at (.+)\. Check for available slots now\? Choose Cancel to start the normal countdown instead\.$/);
    if (match) return `Limit wyszukiwań wygasł o ${match[1]}. Sprawdzić dostępne terminy teraz? Wybierz Anuluj, aby zamiast tego rozpocząć standardowe odliczanie.`;
    match = text.match(/^Rate limit reached — search resumes at (.+)$/);
    if (match) return `Osiągnięto limit wyszukiwań — wznowienie o ${match[1]}`;
    match = text.match(/^Search resumes in (.+)$/);
    if (match) return `Wznowienie wyszukiwania za ${match[1]}`;
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
  window.ikwI18n = { apply, installSwitcher, t, lang, places };
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
