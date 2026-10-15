#ifndef DOUBLY_LINKED_LIST_H
#define DOUBLY_LINKED_LIST_H

#include <string>

class DoublyLinkedList {
    struct Node {
        std::string data;
        Node* prev;
        Node* next;
    };

    Node* head_;
    Node* tail_;
    int size_;

public:
    DoublyLinkedList();
    ~DoublyLinkedList();

    void pushBack(std::string value);
    std::string popBack();
    std::string back();
    bool empty();
    int size();
};

#endif
