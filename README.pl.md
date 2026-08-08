# info-kierowca-notifier

[English](README.md) · [Polski](README.pl.md)

> **Zanim zaczniesz:** to narzędzie służy do przyspieszania już zarezerwowanego egzaminu na prawo jazdy. Potrzebujesz aktywnej rezerwacji i jej aktualnej daty; aplikacja powiadamia tylko o wcześniejszych terminach. Nie służy do znalezienia pierwszej rezerwacji.

Program sprawdza wolne terminy w [info-kierowca.pl](https://info-kierowca.pl), polskim portalu rezerwacji egzaminów na prawo jazdy. Obserwuje dostępność i informuje o wolnym terminie w panelu oraz na telefonie. Samo sprawdzanie jest wyłącznie do odczytu. Opcjonalnie, gdy masz już opłaconą rezerwację i chcesz ją przyspieszyć, program może otworzyć zalogowaną przeglądarkę i przejść do wyboru daty zmiany terminu. Domyślnie wybór nowej daty oraz każde potwierdzenie wykonujesz samodzielnie — nic nie jest automatycznie rezerwowane. Dwa eksperymentalne, domyślnie wyłączone przełączniki w Ustawienia → Automatyzacja mogą wybrać pasujący termin i wysłać zmianę rezerwacji bez kliknięcia użytkownika. Przed ich włączeniem przeczytaj [Jak to działa](#jak-to-działa) i [docs/ADVANCED.md](docs/ADVANCED.md).

![Panel z wolnym terminem](docs/dashboard.png)

## Szybki start

1. Pobierz wersję dla swojego systemu z [strony wydań](../../releases) — bez instalatora, Pythona ani dodatkowej konfiguracji.
2. Uruchom program. Karta przeglądarki otworzy się automatycznie.
3. Zeskanuj kod QR aplikacją mObywatel, aby się zalogować, albo pomiń ten krok i wpisz numer PKK ręcznie.
4. Potwierdź wykryty numer PKK/kategorię prawa jazdy (lub wypełnij je ręcznie), wybierz ośrodek/ośrodki egzaminacyjne, **wpisz wymaganą datę obecnej rezerwacji** i wybierz sposób powiadamiania.

Od tej pory otwarta karta przeglądarki jest Twoim panelem; znajduje się w niej przycisk **Zakończ** do wyłączenia programu.

**Tylko przy pierwszym uruchomieniu:** ponieważ buildy nie są podpisane, Windows/macOS pokaże jednorazowe ostrzeżenie. W Windows wybierz „Więcej informacji” → „Uruchom mimo to”. W macOS kliknij plik prawym przyciskiem i wybierz „Otwórz”.

## Jak to działa

Program sprawdza te same dwa endpointy, których używa strona info-kierowca.pl do wyświetlania terminów. Robi to automatycznie, według zegara, zamiast ręcznego odświeżania strony. Sprawdzanie jest ściśle tylko do odczytu: nie rezerwuje ani nie wykonuje żadnego działania poza sprawdzeniem dostępności.

Jeśli włączysz pomoc przy zmianie terminu (domyślnie włączona, przełącznik `auto_open_browser`), pasujący termin otworzy także zalogowane okno Chrome na ekranie „zmień termin” istniejącej rezerwacji. Domyślnie program zatrzymuje się tam, na pustym wyborze zakresu dat, bez wysyłania danych — nową datę wybierasz i potwierdzasz ręcznie. W Ustawienia → Automatyzacja są dwa kolejne przełączniki, oba domyślnie wyłączone: pierwszy wybiera pasujący termin i przechodzi do podsumowania, drugi — wymagający pierwszego i własnego okna potwierdzenia przed włączeniem — również go potwierdza, faktycznie wysyłając zmianę rezerwacji bez kliknięcia użytkownika. Dokładny opis kliknięć i uzasadnienie znajdują się w [docs/ADVANCED.md](docs/ADVANCED.md). Nigdy nie włączaj drugiej opcji, zanim nie sprawdzisz, że wybór terminu działa niezawodnie.

Sesja logowania trwa około godziny, zanim info-kierowca.pl wymusi ponowne skanowanie kodu QR — to ograniczenie strony, którego narzędzie nie może wydłużyć. Gdy tak się stanie, Chrome otworzy się automatycznie na ekranie logowania; panel pokaże też odliczanie szacowanego wygaśnięcia oraz przycisk wcześniejszego odnowienia. Szczegóły: [automatyczne ponowne logowanie](docs/ADVANCED.md#auto-relogin-on-session-expiry).

Pliki cookie sesji i numer PKK nie trafiają nigdzie poza info-kierowca.pl.

Program korzysta z nieudokumentowanego API, które info-kierowca.pl może w każdej chwili zmienić lub zablokować. Korzystaj na własne ryzyko i zgodnie z regulaminem serwisu.

## Powiadomienia

Podczas konfiguracji otrzymasz prywatny link. Zainstaluj [aplikację ntfy](https://ntfy.sh/app) i zasubskrybuj dokładnie ten link, aby otrzymać powiadomienie push, gdy pojawi się termin w wybranym przedziale.

## Uruchamianie ze źródeł / konfiguracja zaawansowana

Chcesz uruchomić program ze źródeł, używać go w Linuksie z systemd lub poznać szczegóły automatycznego logowania? Zobacz [docs/ADVANCED.md](docs/ADVANCED.md).

## Współpraca

Zgłoszenia i PR-y są mile widziane. To małe narzędzie o jednym celu, dlatego prosimy o skupione zmiany.

## Licencja

MIT — zobacz [LICENSE](LICENSE).
