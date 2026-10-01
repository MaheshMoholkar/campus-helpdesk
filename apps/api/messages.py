"""Fixed replies that are not written by the model, in each supported language.

Abstentions, refusals and confirmations use fixed wording on purpose: these are
the moments where a model improvising would do the most harm.
"""

LANGUAGES = ("en", "hi", "mr", "hinglish")

_TEXT: dict[str, dict[str, str]] = {
    "abstain": {
        "en": "I could not find this in the university's documents, so I would rather not guess. "
        "Please contact {office}{contact}.",
        "hi": "मुझे यह जानकारी विश्वविद्यालय के दस्तावेज़ों में नहीं मिली, इसलिए मैं अनुमान नहीं लगाऊँगा। "
        "कृपया {office} से संपर्क करें{contact}।",
        "mr": "ही माहिती मला विद्यापीठाच्या कागदपत्रांमध्ये सापडली नाही, त्यामुळे मी अंदाज सांगणार नाही. "
        "कृपया {office} शी संपर्क साधा{contact}.",
        "hinglish": "Yeh jaankari mujhe university ke documents mein nahi mili, isliye main guess nahi karunga. "
        "Please {office} se contact karein{contact}.",
    },
    "out_of_scope": {
        "en": "I can only help with questions about Navrang University: fees, exams, hostels, "
        "admissions, placements and your own records.",
        "hi": "मैं केवल नवरंग विश्वविद्यालय से जुड़े प्रश्नों में मदद कर सकता हूँ: शुल्क, परीक्षा, छात्रावास, "
        "प्रवेश, प्लेसमेंट और आपके अपने रिकॉर्ड।",
        "mr": "मी फक्त नवरंग विद्यापीठाशी संबंधित प्रश्नांसाठी मदत करू शकतो: शुल्क, परीक्षा, वसतिगृह, "
        "प्रवेश, प्लेसमेंट आणि तुमचे स्वतःचे रेकॉर्ड.",
        "hinglish": "Main sirf Navrang University se jude sawaalon mein madad kar sakta hoon: fees, exams, "
        "hostel, admission, placement aur aapke apne records.",
    },
    "login_required": {
        "en": "Please log in as a student so I can look up your own records.",
        "hi": "अपने रिकॉर्ड देखने के लिए कृपया छात्र के रूप में लॉग इन करें।",
        "mr": "तुमचे रेकॉर्ड पाहण्यासाठी कृपया विद्यार्थी म्हणून लॉग इन करा.",
        "hinglish": "Apne records dekhne ke liye please student login karein.",
    },
    "students_only": {
        "en": "Personal records (fee dues, attendance, timetable, bonafide requests) are available "
        "for student accounts only.",
        "hi": "व्यक्तिगत रिकॉर्ड (शुल्क, उपस्थिति, समय-सारणी, बोनाफाइड) केवल छात्र खातों के लिए उपलब्ध हैं।",
        "mr": "वैयक्तिक रेकॉर्ड (शुल्क, उपस्थिती, वेळापत्रक, बोनाफाईड) फक्त विद्यार्थी खात्यांसाठी उपलब्ध आहेत.",
        "hinglish": "Personal records (fee dues, attendance, timetable, bonafide) sirf student accounts ke "
        "liye available hain.",
    },
    "confirm_bonafide": {
        "en": "I can submit a bonafide certificate request for you (purpose: {purpose}). "
        "Please confirm to go ahead.",
        "hi": "मैं आपके लिए बोनाफाइड प्रमाणपत्र का अनुरोध भेज सकता हूँ (उद्देश्य: {purpose})। आगे बढ़ने के लिए कृपया पुष्टि करें।",
        "mr": "मी तुमच्यासाठी बोनाफाईड प्रमाणपत्राची विनंती पाठवू शकतो (उद्देश: {purpose}). "
        "पुढे जाण्यासाठी कृपया पुष्टी करा.",
        "hinglish": "Main aapke liye bonafide certificate ki request bhej sakta hoon (purpose: {purpose}). "
        "Aage badhne ke liye please confirm karein.",
    },
    "action_done": {
        "en": "Done. Your bonafide certificate request has been submitted (reference {reference}).",
        "hi": "हो गया। आपका बोनाफाइड प्रमाणपत्र अनुरोध जमा कर दिया गया है (संदर्भ {reference})।",
        "mr": "झाले. तुमची बोनाफाईड प्रमाणपत्राची विनंती सादर केली आहे (संदर्भ {reference}).",
        "hinglish": "Ho gaya. Aapki bonafide certificate request submit ho gayi hai (reference {reference}).",
    },
    "action_invalid": {
        "en": "That request is no longer waiting for confirmation. Please ask again.",
        "hi": "यह अनुरोध अब पुष्टि के लिए लंबित नहीं है। कृपया फिर से पूछें।",
        "mr": "ही विनंती आता पुष्टीसाठी प्रलंबित नाही. कृपया पुन्हा विचारा.",
        "hinglish": "Yeh request ab confirmation ke liye pending nahi hai. Please dobara poochein.",
    },
    "records_unavailable": {
        "en": "I could not reach the student records system just now. Please try again in a while.",
        "hi": "अभी छात्र रिकॉर्ड प्रणाली से संपर्क नहीं हो पाया। कृपया थोड़ी देर बाद प्रयास करें।",
        "mr": "सध्या विद्यार्थी रेकॉर्ड प्रणालीशी संपर्क होऊ शकला नाही. कृपया थोड्या वेळाने प्रयत्न करा.",
        "hinglish": "Abhi student records system se contact nahi ho paya. Please thodi der baad try karein.",
    },
}


def text(key: str, language: str, **values: str) -> str:
    return _TEXT[key].get(language, _TEXT[key]["en"]).format(**values)


def office_contact(office: dict | None) -> tuple[str, str]:
    """(office name, formatted contact details) for the abstain reply."""
    if not office:
        return "the university helpdesk", ""
    details = ", ".join(filter(None, [office.get("email"), office.get("phone"), office.get("hours")]))
    return office["name"], f" ({details})" if details else ""
