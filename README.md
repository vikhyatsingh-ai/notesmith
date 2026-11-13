# Notesmith — YouTube video se class notes ka PDF

Apna Revisemap-jaisa tool. YouTube link daalo → AI notes banaye → PDF download karo.
Poora free stack, koi paid server nahi.

---

## Ismein kya hai

| File | Kaam |
|---|---|
| `app.py` | Poora app — transcript, AI notes, PDF, aur web UI sab isi mein |
| `requirements.txt` | Python packages (Streamlit Cloud khud install karta hai) |
| `packages.txt` | PDF banane ke liye system libraries (Streamlit Cloud khud install karta hai) |
| `README.md` | Yahi guide |

Kuch bhi install ya coding nahi karni — bas 3 file upload karni hain.

---

## Phone se kaise chalu karein (15-20 minute, ek hi baar)

### Step 1 — Gemini ki free API key lo
1. Phone browser mein kholo: `https://aistudio.google.com/apikey`
2. Gmail se login karo → **Create API key** → key copy kar lo (`AIza...` jaisi lambi string).
3. Kahin note kar lo. Yeh free tier hai, card ki zaroorat nahi.

> Chahein to Groq bhi use kar sakte ho (`https://console.groq.com/keys`) — app mein dono option hain.

### Step 2 — GitHub par repo banao
1. `https://github.com` par account banao (phone se ho jata hai).
2. Top-right **+** → **New repository** → naam `notesmith` → **Public** → **Create repository**.
3. **uploading an existing file** link dabao.
4. Phone se `app.py`, `requirements.txt`, `packages.txt` teeno select karke upload karo.
5. **Commit changes** dabao.

### Step 3 — Streamlit par deploy karo
1. `https://share.streamlit.io` kholo → **Continue with GitHub** → allow karo.
2. **Create app** → **Deploy a public app from GitHub**.
3. Repository: `yourname/notesmith`, Branch: `main`, Main file path: `app.py`.
4. **Deploy** dabao. 3-5 minute mein app ka link mil jayega
   (`https://notesmith-yourname.streamlit.app` jaisa). Woh link phone ke home screen par
   bookmark kar lo — bas, app ready hai.

### Step 4 — API key app mein daalo (taaki AI notes banein)
App khulne ke baad: top-right **⋮** menu → **Settings** → **Secrets** → yeh likho:

```toml
GEMINI_API_KEY = "yahan apni AIza... key paste karo"
```

**Save** dabao. App khud restart hoga. Ab har user ke liye key set hai.

> Key Secrets mein daalne se woh GitHub par kabhi nahi jati. Agar key na daalo to app
> chalega, par sirf "offline notes" banayega (transcript ki lines ko arrange karke) —
> quality kam hogi.

---

## App kaise use karna hai

1. YouTube se video kholo → **Share → Copy link**.
2. App mein **Link se** tab mein paste karo → **Notes banao**.
3. 30-60 second mein PDF ready → **PDF download kijiye**.

**Agar link se kaam na kare** ("YouTube ne request rok di" jaisa error aaye): YouTube cloud
servers ke IP block karta hai, yeh normal hai. Us waqt:
- YouTube app/web par video ke neeche **…more → Show transcript** → sab text copy karo,
- app ke **Transcript paste** tab mein paste karo → **Paste se notes banao**.

Notes ki bhasha sidebar se badal sakte ho: Hinglish, हिंदी, ya English.

---

## Andar se kaise kaam karta hai

```
YouTube link
   │
   ├─ youtube-transcript-api   →  captions (free, koi download nahi)
   │
   ├─ Gemini / Groq free API   →  transcript ko structured notes JSON mein badalta hai
   │                              (sections, points, terms, formulae, exam tips, questions)
   │
   └─ WeasyPrint (HTML→PDF)    →  sundar PDF, Devanagari sahi render hoti hai
```

Revisemap bhi asal mein yahi teen step karta hai — unka "credits" system bas in LLM calls
ka paisa hai. Yahan woh free tier se ho jata hai.

**Timestamps:** AI ko transcript `[mm:ss]` ke saath diya jata hai aur woh har section ka
shuruaati quote deta hai; app us quote ko transcript mein dhoondh kar asli timestamp nikalta
hai, jisse PDF mein "▶ 12:34" ka clickable link banta hai jo video ke usi hisse par le jata hai.

---

## Free tier ke limits (honest baat)

- **Gemini free tier:** lagbhag 10-15 requests/minute aur ~250 requests/day (Google kabhi
  badal deta hai). Ek PDF = ek request. Personal use ke liye kaafi.
- **Transcript fetch:** YouTube cloud IPs se requests kabhi-kabhi block karta hai. Solution
  upar likha hai — paste tab. Sabse bharosemand raasta yahi hai.
- **Bade videos:** 60,000 characters se lamba transcript kaat diya jata hai (taaki model
  ka limit na toote).
- **Quality:** AI galti kar sakta hai. PDF ke footer mein bhi yahi likha aata hai — exam se
  pehle textbook se check karo.

---

## Kuch aaye to (troubleshooting)

| Error / dikkat | Matlab aur hal |
|---|---|
| `HTTP 404: model ... no longer available` | Google purane model band kar deta hai. App khud key se available models ki list maang kar naya flash model chun leta hai, isliye aam taur par yeh error nahi aata. Phir bhi aaye to `app.py` mein `GEMINI_PREFER` list mein naya naam jod dijiye. |
| `HTTP 429` / quota | Free tier ki daily limit khatam. Kal tak rukiye, ya sidebar se Groq + Groq key use kariye. |
| `HTTP 403` / `API key not valid` | Key galat ya delete ho chuki hai. AI Studio se nayi key banaiye. |
| `YouTube ne request rok di` | YouTube cloud IP block karta hai. **Transcript paste** tab use kariye. |
| `Is video par koi caption nahi hai` | Us video mein subtitles hi nahi hain — doosra video chuniye. |

## Key delete/rotate karna

Key kisi ke saath share ho gayi ho (chat, screenshot, kahin bhi) to usse turant badal dein:
AI Studio → API keys → us key ke saamne **⋮** → **Delete** → nayi **Create API key** banaiye →
app ke Secrets mein nayi key daal dijiye. Purani key turant band ho jati hai.

## Laptop par chalana (baad mein, chahein to)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Ya bina UI, seedha command se:

```bash
python app.py --cli "https://youtu.be/VIDEO_ID" --lang hinglish --out notes.pdf
GEMINI_API_KEY=AIza... python app.py --cli "https://youtu.be/VIDEO_ID" --out notes.pdf
python app.py --cli "" --transcript-file transcript.txt --out notes.pdf
```

---

## Ek baat saaf

Yeh tool apne notes banane ke liye hai. Kisi ki paid service ka API seedha call karke uska
credit/quota bypass karna uske terms todta hai — is code mein woh jaan-boojh kar nahi kiya
gaya hai; poora pipeline aapke apne free accounts par chalta hai.
