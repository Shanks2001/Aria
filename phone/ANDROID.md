# Aria sul telefono Android

## A) Solo interfaccia (il server resta sul PC)
Vedi README, passo 1. È la via più rapida per provare voce, registrazioni e agenda dal telefono.

## B) Server sul telefono con Termux (nessun PC)
1. Installa **Termux** da F-Droid (la versione del Play Store è obsoleta).
2. In Termux:
   ```bash
   pkg update && pkg install python openssl git
   pip install starlette uvicorn python-multipart
   termux-setup-storage
   # copia qui la cartella del progetto (es. da Download) e poi:
   cd aria && python run.py --https --no-browser
   ```
3. Apri `https://localhost:8000` in Chrome sul telefono, accetta il certificato e usa *Aggiungi a schermata Home*.
4. Modello linguistico (facoltativo): compila **llama.cpp** in Termux, avvia `llama-server -m modello.gguf --port 8080`
   e metti in `.env`: `LLM_BACKEND=openai` e `LLM_URL=http://127.0.0.1:8080`. Un modello da 1-3 miliardi di parametri (quantizzato Q4) è il limite realistico.
5. Per tenerlo vivo in background: Termux → `termux-wake-lock` e disattiva l'ottimizzazione batteria per Termux.

## C) Blocco chiamate reali: app "ponte"
Una PWA non può intercettare chiamate. Serve una piccola app nativa che chieda al server cosa fare.
Dal punto di vista di Aria è solo `POST /api/events` (vedi README). Scheletro **non testato** per il blocco chiamate (Android 10+):

```kotlin
// AriaScreeningService.kt
import android.telecom.Call
import android.telecom.CallScreeningService
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class AriaScreeningService : CallScreeningService() {
    override fun onScreenCall(details: Call.Details) {
        val number = details.handle?.schemeSpecificPart ?: ""
        val action = try { ask(number) } catch (e: Exception) { "allow" }   // se il server non risponde, la chiamata passa
        val r = CallResponse.Builder()
        when (action) {
            "block" -> r.setDisallowCall(true).setRejectCall(true).setSkipNotification(true)
            "warn"  -> r.setSilenceCall(true)
        }
        respondToCall(details, r.build())
    }

    private fun ask(number: String): String {
        val c = URL("http://IP-DEL-SERVER:8000/api/events").openConnection() as HttpURLConnection
        c.requestMethod = "POST"; c.connectTimeout = 2000; c.readTimeout = 2500; c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        c.setRequestProperty("X-Token", "IL-TUO-TOKEN")
        c.outputStream.use { it.write(JSONObject(mapOf("kind" to "call", "sender" to number)).toString().toByteArray()) }
        return JSONObject(c.inputStream.bufferedReader().readText()).getString("action")
    }
}
```

Da fare nell'app:
- `AndroidManifest.xml`: dichiara il servizio con `android:permission="android.permission.BIND_SCREENING_SERVICE"` e l'intent-filter `android.telecom.CallScreeningService`; aggiungi `INTERNET`.
- All'avvio chiedi il ruolo con `RoleManager.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)`: l'utente deve approvarlo.
- Rete: se il server è in HTTP sulla Wi-Fi di casa, consenti il traffico in chiaro per quell'host con una `network_security_config`; fuori casa usa una VPN privata (es. Tailscale) invece di aprire porte sul router.
- La decisione va presa in pochi secondi: tieni una copia locale della lista nera per quando il server non è raggiungibile.
- **SMS**: con un `BroadcastReceiver` per `SMS_RECEIVED` (permesso `RECEIVE_SMS`) invii `{"kind":"sms","sender":..., "text":...}`. Per distribuzione sul Play Store Google limita questi permessi; per uso personale (installazione diretta) va bene.
- **Rubrica**: leggi i contatti e inviali a `POST /api/contacts` così non vengono mai bloccati.
- **Chiamate perse/risposte** (per il report): invia `status:"missed"|"answered"` e `duration` a fine chiamata.

## iPhone
Apple non permette ad app di terze parti di bloccare chiamate in tempo reale né di leggere gli SMS. L'interfaccia (PWA) funziona,
mentre il blocco si limita a liste di numeri con una *Call Directory extension*. Mail via IMAP funziona ovunque.
