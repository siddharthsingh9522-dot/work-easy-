# OM Suite Mail Bridge (Thunderbird extension) v2.0.0

Ek hi extension jo pehle ke do extensions ("Auto Bill Intelligence Bridge" aur "OM Report AI Mail Bridge") ki jagah leta hai.
Purane dono ko remove kar dein.

## Ye kya karta hai
1. **Send jobs:** web app (Mail Center, aage Bill Mail) jo mails queue karta hai unhe aapke Thunderbird account/identity se bhejta hai (attachment ke saath).
2. **Match jobs:** Thunderbird ke Sent folders me purani mails dhoondh kar recipients (To/CC/BCC) ka suggestion deta hai (Bill Mail ke liye).
3. Har user ka apna token hota hai - aapki jobs sirf aapke Thunderbird ko milti hain.

## Install
Thunderbird -> Add-ons and Themes -> gear -> Debug Add-ons -> Load Temporary Add-on -> `manifest.json`.
(Temporary add-on Thunderbird restart par hat jata hai. Permanent install ke liye extension ko Mozilla/Thunderbird add-on portal par "self-distribution" ke liye sign karwana padta hai, phir signed `.xpi` install hota hai - exact steps portal ke developer docs me dekhein.)

## Connect (ek baar)
Web app -> Mail Center -> **Thunderbird setup** page se connection code copy karein -> extension Options me paste karke **Apply code & connect**.
Isse URL, username, token aur auto-send apne aap set ho jate hain. Safety web app me hai: mails sirf tab queue hoti hain jab aap Mail Center me confirm karke Send dabate ho (Test mode default ON).

## Notes
* Thunderbird 102+ chahiye. Extension har 1 minute me queue check karta hai (popup me **Sync** se turant).
* Extension me "Allow automatic Thunderbird send" OFF kar dene par jobs fail hongi ("Auto-send disabled").
* Attachment `compose.addAttachment` ko real `File` object ke roop me diya jata hai (v1.1.1 ka fix).
* Search Folders: Options me chunein kahan purani mails dhoondni hain; kuch na chuna to har account ka Sent folder.
* Naya token chahiye (leak ho gaya?) to web app me "Naya code banao" dabayein - purana turant band ho jata hai.
