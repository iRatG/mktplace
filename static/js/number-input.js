// Ввод сумм и счётчиков с пробелами между разрядами.
//
// Поля с атрибутом data-number-input форматируются при загрузке и при вводе
// («1500000» → «1 500 000»; хвост «.00» убирается). Кнопки −/+ рядом с полем
// (data-step-button) и клавиши ↑/↓ меняют значение на data-step; кнопки скрыты,
// пока не загрузился этот скрипт. Сервер сам убирает пробелы, поэтому без JS
// форма тоже работает.
//
// Элемент data-bloggers-hint внутри формы показывает, на сколько блогеров
// хватает бюджета при текущей цене (budget / fixed_price, с округлением вниз);
// если «Макс. блогеров» больше — поле выделяется красным ещё до отправки.
// Поле с data-min выделяется красным, пока значение меньше минимума (подсказка
// data-min-hint-for тоже краснеет). Живое выделение помечено data-live-invalid —
// static/js/form-errors.js его не снимает.
(function () {
    "use strict";

    function parse(text) {
        var clean = String(text || "").replace(/\s/g, "").replace(",", ".");
        if (clean === "" || isNaN(Number(clean))) return null;
        return Number(clean);
    }

    function format(text) {
        var clean = String(text || "").replace(/\s/g, "").replace(",", ".");
        clean = clean.replace(/[^\d.]/g, "");
        var dot = clean.indexOf(".");
        var intPart = dot === -1 ? clean : clean.slice(0, dot);
        var fracPart = dot === -1 ? null : clean.slice(dot + 1).replace(/\./g, "").slice(0, 2);
        intPart = intPart.replace(/^0+(?=\d)/, "");
        var grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
        return fracPart === null ? grouped : grouped + "." + fracPart;
    }

    function stripZeroFraction(text) {
        return String(text || "").replace(/[.,]0+$/, "");
    }

    function reformat(input) {
        var old = input.value;
        var caret = input.selectionStart;
        // Сколько значащих символов (цифр и точки) стояло до курсора.
        var significant = old.slice(0, caret == null ? old.length : caret).replace(/[^\d.,]/g, "").length;
        var next = format(old);
        if (next === old) return;
        input.value = next;
        if (caret == null || document.activeElement !== input) return;
        var pos = 0, seen = 0;
        while (pos < next.length && seen < significant) {
            if (/[\d.]/.test(next[pos])) seen++;
            pos++;
        }
        input.setSelectionRange(pos, pos);
    }

    function step(input, direction) {
        var size = Number(input.dataset.step || 1);
        var current = parse(input.value) || 0;
        var value = Math.max(0, current + direction * size);
        input.value = format(String(value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function setLiveInvalid(input, on) {
        if (!input) return;
        if (on) {
            input.setAttribute("aria-invalid", "true");
            input.dataset.liveInvalid = "1";
        } else if (input.dataset.liveInvalid) {
            input.removeAttribute("aria-invalid");
            delete input.dataset.liveInvalid;
        }
    }

    function checkMin(input) {
        var min = Number(input.dataset.min);
        var value = parse(input.value);
        var below = value !== null && value > 0 && value < min;
        setLiveInvalid(input, below);
        var hint = input.form && input.form.querySelector('[data-min-hint-for="' + input.name + '"]');
        if (hint) {
            hint.classList.toggle("text-red-600", below);
            hint.classList.toggle("text-gray-500", !below);
        }
    }

    function bloggersWord(n) {
        return n % 10 === 1 && n % 100 !== 11 ? "блогера" : "блогеров";
    }

    function updateHint(hint) {
        var form = hint.closest("form");
        if (!form) return;
        var budget = parse(form.elements.budget && form.elements.budget.value);
        var price = parse(form.elements.fixed_price && form.elements.fixed_price.value);
        var maxBloggers = parse(form.elements.max_bloggers && form.elements.max_bloggers.value);
        var maxInput = form.elements.max_bloggers;
        var paymentType = form.querySelector("input[name=payment_type]:checked");
        if (paymentType && paymentType.value !== "fixed") {
            hint.textContent = "";
            setLiveInvalid(maxInput, false);
            return;
        }
        if (!budget || !price) {
            hint.textContent = "";
            hint.classList.remove("text-red-600");
            setLiveInvalid(maxInput, false);
            return;
        }
        var fits = Math.floor(budget / price);
        var text = "Бюджета хватит на " + fits + " " + bloggersWord(fits) + ".";
        var over = maxBloggers && maxBloggers > fits;
        if (over) text += " Уменьшите «Макс. блогеров» или увеличьте бюджет.";
        hint.textContent = text;
        hint.classList.toggle("text-red-600", !!over);
        hint.classList.toggle("text-gray-600", !over);
        setLiveInvalid(maxInput, !!over);
    }

    function init() {
        var inputs = document.querySelectorAll("input[data-number-input]");
        inputs.forEach(function (input) {
            input.value = format(stripZeroFraction(input.value));
            input.addEventListener("input", function () { reformat(input); });
            if (input.dataset.min) {
                input.addEventListener("input", function () { checkMin(input); });
                checkMin(input);
            }
            input.addEventListener("keydown", function (e) {
                if (e.key === "ArrowUp") { e.preventDefault(); step(input, 1); }
                if (e.key === "ArrowDown") { e.preventDefault(); step(input, -1); }
            });
            var group = input.closest("[data-number-group]");
            if (!group) return;
            group.querySelectorAll("[data-step-button]").forEach(function (button) {
                button.classList.remove("hidden");
                button.addEventListener("click", function () {
                    step(input, Number(button.dataset.stepButton));
                });
            });
        });

        var hints = document.querySelectorAll("[data-bloggers-hint]");
        hints.forEach(function (hint) {
            var form = hint.closest("form");
            if (!form) return;
            ["input", "change"].forEach(function (type) {
                form.addEventListener(type, function () { updateHint(hint); });
            });
            updateHint(hint);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
