// EN / FA i18n
window.I18N = {
  en: {
    newChat: "New chat",
    welcomeTitle: "How can I help you today?",
    welcomeSubtitle: "Ask anything — I can search the web, run code, understand images, and create pictures.",
    sug1: "Search latest AI news",
    sug2: "Run a Python script",
    sug3: "Generate an image",
    sug4: "Explain a concept",
    inputPlaceholder: "Message Kimi...",
    hint: "Kimi may make mistakes. Verify important info.",
    thinking: "Thinking",
    searching: "Searching the web",
    running: "Running code",
    analyzing: "Analyzing image",
    generatingImage: "Generating image",
    copy: "Copy",
    copied: "Copied!",
    run: "Run",
    edit: "Edit",
    save: "Save",
    cancel: "Cancel",
    delete: "Delete",
    toolResult: "Tool result",
    deleted: "Deleted",
    confirmDelete: "Delete this chat?",
    error: "Error",
    manageUsers: "Manage users",
    switchLang: "Language",
    toggleTheme: "Theme",
    logout: "Logout",
    usersTitle: "Users",
    htmlPreview: "HTML Preview",
    openInWindow: "Open in window",
    emptyReply: "(no answer)",
  },
  fa: {
    newChat: "چت جدید",
    welcomeTitle: "امروز چطور می‌تونم کمکت کنم؟",
    welcomeSubtitle: "هرچی خواستی بپرس — می‌تونم وب رو جستجو کنم، کد اجرا کنم، تصویرها رو بفهمم و عکس بسازم.",
    sug1: "جستجوی آخرین اخبار هوش مصنوعی",
    sug2: "اجرای اسکریپت پایتون",
    sug3: "ساخت یک تصویر",
    sug4: "توضیح یک مفهوم",
    inputPlaceholder: "پیام به کیمی...",
    hint: "ممکنه اشتباه کنم. اطلاعات مهم رو راستی‌آزمایی کن.",
    thinking: "در حال فکر کردن",
    searching: "در حال جستجوی وب",
    running: "در حال اجرای کد",
    analyzing: "در حال تحلیل تصویر",
    generatingImage: "در حال ساخت تصویر",
    copy: "کپی",
    copied: "کپی شد!",
    run: "اجرا",
    edit: "ویرایش",
    save: "ذخیره",
    cancel: "انصراف",
    delete: "حذف",
    toolResult: "نتیجه ابزار",
    deleted: "حذف شد",
    confirmDelete: "این چت حذف بشه؟",
    error: "خطا",
    manageUsers: "مدیریت کاربران",
    switchLang: "تغییر زبان",
    toggleTheme: "تم",
    logout: "خروج",
    usersTitle: "کاربران",
    htmlPreview: "پیش‌نمایش HTML",
    openInWindow: "باز کردن در پنجره جدید",
    emptyReply: "(پاسخی دریافت نشد)",
  },
};

window.t = function (key) {
  const lang = document.documentElement.getAttribute("data-lang") || "en";
  return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
};

window.applyI18n = function () {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
  });
};
