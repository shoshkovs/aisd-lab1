#include <iostream>
#include <string>
#include "DynamicArray.h"
#include "Stack.h"

bool isNumber(std::string token) {
    if (token.size() == 0) return false;
    return token[0] >= '0' && token[0] <= '9';
}

bool isOperator(std::string token) {
    return token == "+" || token == "-" || token == "*"
        || token == "/" || token == "^";
}

bool isFunction(std::string token) {
    return token == "sin" || token == "cos";
}

int priority(std::string op) {
    if (op == "+" || op == "-") return 1;
    if (op == "*" || op == "/") return 2;
    if (op == "^") return 3;
    return 0;
}

bool isRightAssoc(std::string op) {
    return op == "^";
}

bool isLetter(char ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

char toLowerChar(char ch) {
    if (ch >= 'A' && ch <= 'Z') {
        return ch - 'A' + 'a';
    }
    return ch;
}

bool tokenize(std::string input, DynamicArray& tokens) {
    int i = 0;
    while (i < (int)input.size()) {
        char ch = input[i];

        if (ch == ' ' || ch == '\t') {
            i++;
            continue;
        }

        if (ch >= '0' && ch <= '9') {
            std::string number = "";
            while (i < (int)input.size() && input[i] >= '0' && input[i] <= '9') {
                number += input[i];
                i++;
            }
            tokens.pushBack(number);
            continue;
        }

        if (ch == '+' || ch == '-' || ch == '*' || ch == '/' || ch == '^'
            || ch == '(' || ch == ')') {
            std::string symbol = "";
            symbol += ch;
            tokens.pushBack(symbol);
            i++;
            continue;
        }

        if (isLetter(ch)) {
            std::string name = "";
            while (i < (int)input.size() && isLetter(input[i])) {
                name += toLowerChar(input[i]);
                i++;
            }
            if (!isFunction(name)) {
                std::cerr << "Ошибка: неизвестное имя " << name << std::endl;
                return false;
            }
            tokens.pushBack(name);
            continue;
        }

        std::cerr << "Ошибка: недопустимый символ " << ch << std::endl;
        return false;
    }
    return true;
}

bool shuntingYard(DynamicArray& tokens, DynamicArray& output) {
    Stack stack;

    bool expectOperand = true;
    bool afterFunction = false;

    for (int i = 0; i < tokens.size(); i++) {
        std::string token = tokens.get(i);

        if (afterFunction) {
            if (token != "(") {
                std::cerr << "Ошибка: после функции должна быть '('" << std::endl;
                return false;
            }
            afterFunction = false;
        }

        if (isNumber(token)) {
            if (!expectOperand) {
                std::cerr << "Ошибка: неожиданное число " << token << std::endl;
                return false;
            }
            output.pushBack(token);
            expectOperand = false;
        }
        else if (isFunction(token)) {
            if (!expectOperand) {
                std::cerr << "Ошибка: неожиданная функция " << token << std::endl;
                return false;
            }
            stack.push(token);
            afterFunction = true;
        }
        else if (isOperator(token)) {
            if (expectOperand) {
                std::cerr << "Ошибка: неожиданный оператор " << token << std::endl;
                return false;
            }
            while (!stack.empty()
                   && isOperator(stack.top())
                   && (priority(stack.top()) > priority(token)
                       || (priority(stack.top()) == priority(token) && !isRightAssoc(token)))) {
                output.pushBack(stack.pop());
            }
            stack.push(token);
            expectOperand = true;
        }
        else if (token == "(") {
            if (!expectOperand) {
                std::cerr << "Ошибка: неожиданная '('" << std::endl;
                return false;
            }
            stack.push(token);
            expectOperand = true;
        }
        else if (token == ")") {
            if (expectOperand) {
                std::cerr << "Ошибка: неожиданная ')'" << std::endl;
                return false;
            }
            while (!stack.empty() && stack.top() != "(") {
                output.pushBack(stack.pop());
            }
            if (stack.empty()) {
                std::cerr << "Ошибка: нет парной '(' для ')'" << std::endl;
                return false;
            }
            stack.pop();
            if (!stack.empty() && isFunction(stack.top())) {
                output.pushBack(stack.pop());
            }
            expectOperand = false;
        }
    }

    if (expectOperand) {
        std::cerr << "Ошибка: выражение не завершено" << std::endl;
        return false;
    }

    while (!stack.empty()) {
        if (stack.top() == "(") {
            std::cerr << "Ошибка: нет парной ')' для '('" << std::endl;
            return false;
        }
        output.pushBack(stack.pop());
    }

    return true;
}

int main() {
    std::cout << "Введите выражение (пустая строка — выход):" << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) break;

        DynamicArray tokens;
        if (!tokenize(line, tokens)) return 1;

        DynamicArray rpn;
        if (!shuntingYard(tokens, rpn)) return 1;

        std::cout << line << "\n\n";
        for (int i = 0; i < rpn.size(); i++) {
            std::cout << rpn.get(i);
            if (i + 1 < rpn.size()) {
                std::cout << ", ";
            }
        }
        std::cout << std::endl;
    }

    return 0;
}
