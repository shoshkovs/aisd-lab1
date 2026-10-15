#ifndef STACK_H
#define STACK_H

#include <string>
#include "DoublyLinkedList.h"

class Stack {
    DoublyLinkedList list_;

public:
    void push(std::string value) {
        list_.pushBack(value);
    }

    std::string pop() {
        return list_.popBack();
    }

    std::string top() {
        return list_.back();
    }

    bool empty() {
        return list_.empty();
    }
};

#endif
