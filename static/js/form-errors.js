// Ошибка поля уходит, как только его исправляют (QA camp_test_3, шаги 1.6 и 12.3).
//
// Ошибки рисует сервер: у поля aria-invalid="true", под ним <p data-error-for="имя">. При вводе или изменении
// поля снимаются его ошибки и ошибки связанных полей: группы задаёт форма атрибутом
// data-error-groups="a b c|d e" (цена и бюджет проверяются вместе, поэтому правка бюджета снимает и ошибку цены).
// Проверку это не отменяет: при отправке сервер проверит всё заново.
(function () {
    "use strict";

    function groupsOf(form) {
        return (form.dataset.errorGroups || "").split("|").map(function (g) {
            return g.trim().split(/\s+/).filter(Boolean);
        });
    }

    function clear(form, name) {
        form.querySelectorAll('[data-error-for="' + name + '"]').forEach(function (el) { el.remove(); });
        form.querySelectorAll('[name="' + name + '"][aria-invalid="true"]').forEach(function (el) {
            if (!el.dataset.liveInvalid) el.removeAttribute("aria-invalid");
        });
    }

    function init(form) {
        var groups = groupsOf(form);
        function onEdit(e) {
            var name = e.target && e.target.name;
            if (!name) return;
            var related = [name];
            groups.forEach(function (g) {
                if (g.indexOf(name) !== -1) related = related.concat(g);
            });
            related.forEach(function (n) { clear(form, n); });
        }
        form.addEventListener("input", onEdit);
        form.addEventListener("change", onEdit);
    }

    function start() {
        document.querySelectorAll("form[data-error-groups]").forEach(init);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
