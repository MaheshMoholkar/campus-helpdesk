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
        "en": "I can only help with questions about your college: fees, exams, hostels, admissions, "
        "placements, and your own CampusERP records.",
        "hi": "मैं केवल आपके कॉलेज से जुड़े प्रश्नों में मदद कर सकता हूँ: शुल्क, परीक्षा, छात्रावास, प्रवेश, "
        "प्लेसमेंट और CampusERP में आपके अपने रिकॉर्ड।",
        "mr": "मी फक्त तुमच्या महाविद्यालयाशी संबंधित प्रश्नांसाठी मदत करू शकतो: शुल्क, परीक्षा, वसतिगृह, "
        "प्रवेश, प्लेसमेंट आणि CampusERP मधील तुमचे स्वतःचे रेकॉर्ड.",
        "hinglish": "Main sirf aapke college se jude sawaalon mein madad kar sakta hoon: fees, exams, hostel, "
        "admission, placement aur CampusERP mein aapke apne records.",
    },
    "login_required": {
        "en": "Please log in to CampusERP so I can look up your own records.",
        "hi": "अपने रिकॉर्ड देखने के लिए कृपया CampusERP में लॉग इन करें।",
        "mr": "तुमचे रेकॉर्ड पाहण्यासाठी कृपया CampusERP मध्ये लॉग इन करा.",
        "hinglish": "Apne records dekhne ke liye please CampusERP mein login karein.",
    },
    "students_only": {
        "en": "Your login is not linked to a student or staff record in CampusERP, so I have no personal "
        "records to look up.",
        "hi": "आपका लॉगिन CampusERP में किसी छात्र या कर्मचारी रिकॉर्ड से जुड़ा नहीं है, इसलिए देखने के लिए कोई "
        "व्यक्तिगत रिकॉर्ड नहीं है।",
        "mr": "तुमचे लॉगिन CampusERP मधील कोणत्याही विद्यार्थी किंवा कर्मचारी रेकॉर्डशी जोडलेले नाही, त्यामुळे "
        "पाहण्यासाठी वैयक्तिक रेकॉर्ड नाहीत.",
        "hinglish": "Aapka login CampusERP mein kisi student ya staff record se linked nahi hai, isliye "
        "dekhne ke liye koi personal records nahi hain.",
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
    "action_failed": {
        "en": "CampusERP did not accept the request, so nothing was submitted. Please ask the college office.",
        "hi": "CampusERP ने अनुरोध स्वीकार नहीं किया, इसलिए कुछ भी जमा नहीं हुआ। कृपया कॉलेज कार्यालय से संपर्क करें।",
        "mr": "CampusERP ने विनंती स्वीकारली नाही, त्यामुळे काहीही सादर झाले नाही. कृपया महाविद्यालय कार्यालयाशी संपर्क साधा.",
        "hinglish": "CampusERP ne request accept nahi ki, isliye kuch submit nahi hua. Please college office se contact karein.",
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
